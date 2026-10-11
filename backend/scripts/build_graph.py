#!/usr/bin/env python3
"""PHASE 1 — Build the real, scored street graph for the Chhaon pilot area.

Runs OFFLINE on your laptop (never in the Lambda). Downloads the walking
network and features from OpenStreetMap with osmnx, computes every edge
attribute, and writes `graph.json.gz` in the same format the Lambda expects.
Upload that file to S3 and swap the graph with no code changes.

Requires the heavy offline deps (see scripts/requirements-offline.txt):
    pip install -r scripts/requirements-offline.txt
(osmnx, geopandas, shapely, pyproj, rasterio, pvlib/astral, pandas)

Everything here is an ESTIMATE built from map data and simple geometric models.

Usage:
    python scripts/build_graph.py --out data/graph.json.gz
    python scripts/build_graph.py --lst-raster lst.tif            # Landsat LST heat
    python scripts/build_graph.py --no-upload --bucket ""          # local only
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

# --- Heavy offline deps ------------------------------------------------------
try:
    import osmnx as ox
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, Point, Polygon, box
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
except Exception as exc:  # pragma: no cover - offline only
    print("Missing offline dependencies. Install them with:\n"
          "  pip install -r scripts/requirements-offline.txt\n"
          f"Import error: {exc}", file=sys.stderr)
    raise SystemExit(2)

# --- Pilot area / defaults ---------------------------------------------------
DEFAULT_PILOT = "Connaught Place, Delhi"
DEFAULT_CENTRE = (28.6314, 77.2197)   # lat, lon
DEFAULT_RADIUS_M = 1000
DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_DATE = "2026-05-15"           # representative hot day
HOURS = list(range(8, 19))            # 8..18 inclusive

DEFAULT_BUILDING_HEIGHT_M = 9.0
LEVELS_TO_METRES = 3.0
TREE_CANOPY_BUFFER_M = 4.0
EDGE_TREE_BUFFER_M = 12.0
HEAT_BUFFER_M = 60.0
POLLUTION_NEAR_ROAD_M = 50.0

POLLUTION_BY_HIGHWAY = {
    "motorway": 1.0, "motorway_link": 1.0, "trunk": 1.0, "trunk_link": 1.0,
    "primary": 0.85, "primary_link": 0.85, "secondary": 0.7,
    "secondary_link": 0.7, "tertiary": 0.5, "tertiary_link": 0.5,
    "residential": 0.25, "unclassified": 0.3, "service": 0.3,
    "living_street": 0.2, "footway": 0.1, "path": 0.1, "pedestrian": 0.1,
    "steps": 0.1, "cycleway": 0.1, "track": 0.15,
}
HIGH_ROADS = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}


def log(msg: str) -> None:
    print(f"[build_graph] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Solar geometry
# ---------------------------------------------------------------------------
def solar_table(lat, lon, date_str, tz_name, hours):
    """Return {hour: {"altitude": deg, "azimuth": deg}} using pvlib (or astral)."""
    try:
        import pvlib
        import pandas as pd
        from zoneinfo import ZoneInfo
        idx = pd.date_range(f"{date_str} 00:00", periods=24, freq="h", tz=ZoneInfo(tz_name))
        sp = pvlib.solarposition.get_solarposition(idx, lat, lon)
        out = {}
        for h in hours:
            out[h] = {
                "altitude": float(sp["apparent_elevation"].iloc[h]),
                "azimuth": float(sp["azimuth"].iloc[h]),
            }
        return out, "pvlib"
    except Exception:
        pass
    # astral fallback (approximate, no atmosphere correction)
    from astral import LocationInfo
    from astral.sun import sun  # noqa: F401
    from astral import moon  # noqa: F401
    import astral
    obs = (lat, lon)
    out = {}
    for h in hours:
        try:
            import datetime as _dt
            from astral import Observer
            o = Observer(lat, lon)
            # astral does not give altitude directly; approximate with NOAA.
            alt, az = _noaa_altaz(lat, lon, date_str, h)
        except Exception:
            alt, az = _noaa_altaz(lat, lon, date_str, h)
        out[h] = {"altitude": alt, "azimuth": az}
    return out, "astral/noaa-approx"


def _noaa_altaz(lat, lon, date_str, hour):
    """Simple NOAA solar position approximation (fallback)."""
    y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    # day of year
    doy = datetime(y, m, d).timetuple().tm_yday
    # fractional year (radians)
    gamma = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    # local solar time (minutes)
    tz_offset_min = 330  # IST = UTC+5:30 (pilot-area assumption)
    time_offset = eqtime + 4 * lon - tz_offset_min
    tst = hour * 60 + time_offset
    ha = math.radians(tst / 4 - 180)
    lat_r = math.radians(lat)
    cos_zen = (math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(ha))
    cos_zen = max(-1.0, min(1.0, cos_zen))
    alt = 90 - math.degrees(math.acos(cos_zen))
    # azimuth
    az_num = -(math.sin(lat_r) * cos_zen - math.sin(decl))
    az_den = math.cos(lat_r) * math.sin(math.acos(cos_zen))
    az = 180 + math.degrees(math.atan2(az_num, az_den + 1e-9))
    return alt, (az % 360)


def solar_intensity_by_hour(solar, hours):
    return [round(max(0.0, min(1.0, math.sin(math.radians(solar[h]["altitude"])))), 4)
            for h in hours]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def shadow_polygon(footprint: Polygon, height_m: float, azimuth: float, altitude: float):
    """Shadow = convex hull of the footprint and its ground projection."""
    if altitude <= 1.0:
        return None
    shadow_len = height_m / math.tan(math.radians(altitude))
    shadow_len = min(shadow_len, 500.0)  # clamp extreme low-sun shadows
    # Sun azimuth is the direction toward the sun; the shadow points opposite.
    opp = math.radians((azimuth + 180.0) % 360.0)
    dx = shadow_len * math.sin(opp)
    dy = shadow_len * math.cos(opp)
    from shapely.affinity import translate
    moved = translate(footprint, xoff=dx, yoff=dy)
    return footprint.union(moved).convex_hull


def safe_union(geoms):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return None
    try:
        return unary_union(geoms)
    except Exception:
        return None


def building_height_m(row) -> tuple[float, str]:
    """height tag -> building:levels*3 -> documented default. Returns (m, method)."""
    h = row.get("height")
    if h is not None and not (isinstance(h, float) and math.isnan(h)):
        try:
            val = float(str(h).replace("m", "").strip())
            if val > 0:
                return val, "height_tag"
        except Exception:
            pass
    lv = row.get("building:levels")
    if lv is not None and not (isinstance(lv, float) and math.isnan(lv)):
        try:
            val = float(str(lv).strip())
            if val > 0:
                return val * LEVELS_TO_METRES, "levels"
        except Exception:
            pass
    return DEFAULT_BUILDING_HEIGHT_M, "default"


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------
def download_all(centre, radius, cache_dir):
    ox.settings.use_cache = True
    ox.settings.cache_folder = cache_dir
    ox.settings.log_console = False

    log(f"downloading walk network (r={radius} m) ...")
    G = ox.graph_from_point(centre, dist=radius, network_type="walk", simplify=True)

    tags = {
        "building": True,
        "natural": ["tree", "wood", "water", "scrub", "grassland"],
        "landuse": ["grass", "forest", "meadow", "village_green", "recreation_ground"],
        "leisure": ["park", "garden", "pitch", "common"],
    }
    log("downloading buildings / greenery / water ...")
    try:
        feats = ox.features_from_point(centre, tags=tags, dist=radius)
    except Exception as exc:
        log(f"feature download failed ({exc}); continuing with no features")
        feats = gpd.GeoDataFrame(geometry=[])
    return G, feats


# ---------------------------------------------------------------------------
# Edge attribute computation
# ---------------------------------------------------------------------------
def build(centre, radius, date_str, tz_name, lst_raster, cache_dir, pilot_name):
    lat0, lon0 = centre
    solar, solar_src = solar_table(lat0, lon0, date_str, tz_name, HOURS)
    si = solar_intensity_by_hour(solar, HOURS)

    G, feats = download_all(centre, radius, cache_dir)

    # Project to a metric CRS for all geometry work.
    Gp = ox.project_graph(G)
    crs = Gp.graph["crs"]
    log(f"projected CRS: {crs}")

    # Features: keep only those that intersect the pilot disc.
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def to_xy(geom):
        return gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(crs).iloc[0]

    # centre in projected coords
    cx, cy = tf.transform(lon0, lat0)
    disc = Point(cx, cy).buffer(radius)

    # Split features
    buildings, greens, waters, tree_points = [], [], [], []
    if len(feats):
        for _, row in feats.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            gxy = to_xy(geom)
            b = row.get("building")
            nat = row.get("natural")
            lu = row.get("landuse")
            lei = row.get("leisure")
            if b is not None and not (isinstance(b, float) and math.isnan(b)):
                if gxy.geom_type in ("Polygon", "MultiPolygon"):
                    h, _m = building_height_m(row)
                    buildings.append((gxy, h))
            elif nat == "water":
                waters.append(gxy)
            elif nat == "tree" or (gxy.geom_type == "Point" and nat == "tree"):
                tree_points.append(gxy.buffer(TREE_CANOPY_BUFFER_M))
            elif nat in ("wood", "grassland", "scrub") or lu in ("grass", "forest", "meadow", "village_green", "recreation_ground") or lei in ("park", "garden"):
                greens.append(gxy)
        # multipolygons from the frame may already be merged; explode polygons
        buildings = [(g, h) for g, h in buildings]
    log(f"features: {len(buildings)} buildings, {len(greens)} green, "
        f"{len(waters)} water, {len(tree_points)} trees")

    green_union = safe_union(greens + waters)
    tree_union = safe_union(tree_points)

    # --- Building shadows per hour -----------------------------------------
    log("computing building shadows per hour ...")
    shadow_by_hour = {}
    for h in HOURS:
        az, alt = solar[h]["azimuth"], solar[h]["altitude"]
        polys = []
        for fp, hh in buildings:
            try:
                sp = shadow_polygon(fp, hh, az, alt)
                if sp is not None and not sp.is_empty:
                    polys.append(sp)
            except Exception:
                continue
        shadow_by_hour[h] = polys
    all_shadows = [p for v in shadow_by_hour.values() for p in v]
    total_shadow_polys = len(all_shadows)
    log(f"  {total_shadow_polys} shadow polygons across {len(HOURS)} hours")

    # --- Edges --------------------------------------------------------------
    edges_gdf = ox.graph_to_gdfs(Gp, nodes=False).reset_index()
    nodes_gdf = ox.graph_to_gdfs(Gp, nodes=True)[0].reset_index()
    # node id -> lon/lat (for output)
    node_wgs = ox.graph_to_gdfs(G, nodes=True)[0]
    node_ll = {nid: (float(row.geometry.y), float(row.geometry.x))
               for nid, row in node_wgs.iterrows()}

    log(f"scoring {len(edges_gdf)} edges ...")
    t0 = time.time()
    lst_data = _load_lst(lst_raster, crs) if lst_raster else None

    # Pre-index the major roads once, for the pollution proximity bonus.
    high_road_geoms = []
    try:
        for _, r in edges_gdf.iterrows():
            if _norm_highway(r.get("highway")) in HIGH_ROADS:
                g = r.geometry
                high_road_geoms.append(g if g.geom_type != "MultiLineString"
                                       else LineString([pt for part in g.geoms for pt in part.coords]))
    except Exception:
        high_road_geoms = []
    high_road_tree = STRtree(high_road_geoms) if high_road_geoms else None

    out_edges = []
    method_counts = {"shade_building": 0, "shade_tree": 0, "heat_lst": 0, "heat_proxy": 0}
    n = len(edges_gdf)
    for k, (_, row) in enumerate(edges_gdf.iterrows()):
        if n > 500 and k % max(1, n // 50) == 0:
            pct = 100 * k / n
            sys.stderr.write(f"\r  edges {k}/{n} ({pct:4.0f}%)")
            sys.stderr.flush()
        geom = row.geometry
        if geom.geom_type == "MultiLineString":
            geom = LineString([pt for part in geom.geoms for pt in part.coords])
        length = float(geom.length)
        if length <= 0:
            continue
        u = int(row["u"]); v = int(row["v"])
        highway = _norm_highway(row.get("highway"))

        # (a) building shade per hour
        building_shade = {}
        for h in HOURS:
            polys = shadow_by_hour[h]
            if not polys:
                building_shade[h] = 0.0
                continue
            tree = STRtree(polys)
            cand = [polys[i] for i in tree.query(geom)]
            u_ = safe_union(cand)
            building_shade[h] = _line_shaded_fraction(geom, u_)
        if any(v_ > 0 for v_ in building_shade.values()):
            method_counts["shade_building"] += 1

        # (b) tree shade (constant across hours)
        tree_shade = 0.0
        if tree_union is not None:
            buf = geom.buffer(EDGE_TREE_BUFFER_M)
            inter = buf.intersection(tree_union)
            tree_shade = min(1.0, inter.area / buf.area) if buf.area > 0 else 0.0
        if green_union is not None:
            buf = geom.buffer(EDGE_TREE_BUFFER_M)
            inter = buf.intersection(green_union)
            green_frac_edge = min(1.0, inter.area / buf.area) if buf.area > 0 else 0.0
            tree_shade = 1 - (1 - tree_shade) * (1 - green_frac_edge * 0.6)
        if tree_shade > 0:
            method_counts["shade_tree"] += 1

        shade_by_hour = [round(1 - (1 - building_shade[h]) * (1 - tree_shade), 4)
                         for h in HOURS]

        # heat
        heat_buf = geom.buffer(HEAT_BUFFER_M)
        if lst_data is not None:
            heat, heat_method = _lst_heat(lst_data, geom, heat_buf)
            method_counts["heat_lst"] += 1
        else:
            built_frac = _area_share(heat_buf, [b for b, _ in buildings])
            green_frac = _area_share(heat_buf, greens + waters)
            heat = round(min(1.0, max(0.0, 0.6 * built_frac + 0.4 * (1 - green_frac))), 4)
            heat_method = "proxy_built_green"
            method_counts["heat_proxy"] += 1

        # pollution
        pollution = _pollution(geom, highway, high_road_geoms, high_road_tree)

        geometry = [[round(x, 5), round(y, 5)] for x, y in geom.coords]
        node_u = node_ll.get(u); node_v = node_ll.get(v)
        # Output geometry must be lon/lat.
        geometry_ll = _project_back(geometry, tf)
        out_edges.append({
            "u": u, "v": v,
            "length_m": round(length, 2),
            "highway": highway,
            "shade_by_hour": shade_by_hour,
            "tree_shade": round(tree_shade, 4),
            "building_shade_by_hour": [round(building_shade[h], 4) for h in HOURS],
            "heat": heat,
            "pollution": pollution,
            "geometry": geometry_ll,
        })
    sys.stderr.write("\r" + " " * 40 + "\r")

    # --- Nodes (output in lon/lat) -----------------------------------------
    nodes_out = []
    for nid, (lat, lon) in node_ll.items():
        nodes_out.append({"id": int(nid), "lat": round(lat, 7), "lon": round(lon, 7)})

    lats = [x["lat"] for x in nodes_out]
    lons = [x["lon"] for x in nodes_out]
    heat_method_used = "lst_raster_zonal_mean" if lst_data is not None else "proxy_0.6*built+0.4*(1-green)"
    shade_method = ("building shadow convex-hull + tree/park 12m buffer overlap; "
                    "shade = 1-(1-building)(1-tree)")
    meta = {
        "pilot": pilot_name,
        "centre": [lat0, lon0],
        "bbox": [min(lats), min(lons), max(lats), max(lons)],
        "radius_m": radius,
        "hours": HOURS,
        "date_used": date_str,
        "timezone": tz_name,
        "built": datetime.now(timezone.utc).isoformat(),
        "is_sample": False,
        "solar_intensity_by_hour": si,
        "solar_source": solar_src,
        "walker_speeds_kmh": {"adult": 5.0, "vendor": 4.5, "elderly": 3.5, "child": 3.5},
        "defaults": {"building_height_m": DEFAULT_BUILDING_HEIGHT_M,
                     "levels_to_metres": LEVELS_TO_METRES,
                     "tree_canopy_buffer_m": TREE_CANOPY_BUFFER_M},
        "sources": ["OpenStreetMap contributors (ODbL)"] + (["Landsat LST raster"] if lst_data else []),
        "methods": {
            "shade": shade_method + f" [{method_counts['shade_building']} edges with building shade, "
                                    f"{method_counts['shade_tree']} with tree shade]",
            "heat": heat_method_used,
            "pollution": "road-class proxy + 50m primary-road bonus (not a measurement)",
            "building_height": "height tag -> building:levels*3 -> default 9m",
            "solar_intensity": f"sin(altitude) via {solar_src}",
        },
        "build_seconds": round(time.time() - t0, 1),
    }
    return {"meta": meta, "nodes": nodes_out, "edges": out_edges}


def _norm_highway(hw):
    if hw is None:
        return "unclassified"
    if isinstance(hw, list):
        hw = hw[0] if hw else "unclassified"
    return str(hw)


def _project_back(coords_xy, transformer):
    """coords_xy are [x,y] in projected CRS; return [lon,lat]."""
    out = []
    for x, y in coords_xy:
        lon, lat = transformer.transform(x, y, direction="INVERSE")
        out.append([round(lon, 5), round(lat, 5)])
    return out


def _line_shaded_fraction(line: LineString, poly) -> float:
    if poly is None or poly.is_empty:
        return 0.0
    try:
        inter = line.intersection(poly)
    except Exception:
        return 0.0
    if inter.is_empty:
        return 0.0
    total = line.length
    return min(1.0, inter.length / total) if total > 0 else 0.0


def _area_share(buffer_geom, geoms) -> float:
    if not geoms or buffer_geom.area <= 0:
        return 0.0
    tree = STRtree(geoms)
    cand = [geoms[i] for i in tree.query(buffer_geom)]
    u = safe_union(cand)
    if u is None:
        return 0.0
    return min(1.0, buffer_geom.intersection(u).area / buffer_geom.area)


def _pollution(line, highway, high_road_geoms, high_road_tree) -> float:
    base = POLLUTION_BY_HIGHWAY.get(highway, 0.3)
    bonus = 0.0
    if high_road_tree is not None:
        try:
            near = line.buffer(POLLUTION_NEAR_ROAD_M)
            for i in high_road_tree.query(near):
                if high_road_geoms[i] is not line:
                    bonus = 0.1
                    break
        except Exception:
            pass
    return round(min(1.0, base + bonus), 4)


def _load_lst(path, crs):
    import rasterio
    from rasterio.warp import reproject, Resampling
    with rasterio.open(path) as src:
        data = src.read(1).astype("float32")
        nodata = src.nodata
        transform = src.transform
        src_crs = src.crs
    if nodata is not None:
        data = np.where(data == nodata, np.nan, data)
    # Normalise to 0..1 using robust percentiles.
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return None
    lo, hi = np.percentile(finite, [2, 98])
    norm = np.clip((data - lo) / max(1e-6, hi - lo), 0, 1)
    return {"norm": norm, "transform": transform, "crs": src_crs}


def _lst_heat(lst, line, buf):
    """Sample the LST raster along the edge (normalised 0-1)."""
    try:
        from shapely.geometry import Point as _P
        xs, ys = line.xy
        vals = []
        for x, y in zip(xs, ys):
            col = int((x - lst["transform"].c) / lst["transform"].a)
            row = int((y - lst["transform"].f) / lst["transform"].e)
            if 0 <= row < lst["norm"].shape[0] and 0 <= col < lst["norm"].shape[1]:
                v = lst["norm"][row, col]
                if np.isfinite(v):
                    vals.append(float(v))
        if vals:
            return round(sum(vals) / len(vals), 4), "lst_raster_zonal_mean"
    except Exception:
        pass
    return 0.5, "lst_raster_zonal_mean"


# ---------------------------------------------------------------------------
# Upload / summary
# ---------------------------------------------------------------------------
def write_graph(graph, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = json.dumps(graph, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(raw)
    return len(raw)


def upload(path, bucket, key):
    import boto3
    boto3.client("s3").upload_file(path, bucket, key)
    log(f"uploaded to s3://{bucket}/{key}")


def summary(graph):
    meta, edges = graph["meta"], graph["edges"]
    if not edges:
        log("no edges built")
        return
    mean_shade = [sum(e["shade_by_hour"][i] for e in edges) / len(edges)
                  for i in range(len(meta["hours"]))]
    print("\n=== Chhaon graph summary ===")
    print(f"pilot      : {meta['pilot']}")
    print(f"nodes      : {len(graph['nodes'])}")
    print(f"edges      : {len(edges)}")
    print(f"bbox       : {meta['bbox']}")
    print(f"date used  : {meta['date_used']}  (solar via {meta['solar_source']})")
    print("mean shade : " + ", ".join(f"{h}:{v:.2f}" for h, v in zip(meta["hours"], mean_shade)))
    print(f"heat method: {meta['methods']['heat']}")
    print(f"shade meth : {meta['methods']['shade']}")
    print(f"build time : {meta['build_seconds']} s")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the real Chhaon scored graph.")
    ap.add_argument("--pilot", default=DEFAULT_PILOT)
    ap.add_argument("--centre", nargs=2, type=float, default=list(DEFAULT_CENTRE),
                    metavar=("LAT", "LON"))
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M)
    ap.add_argument("--date", default=DEFAULT_DATE)
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--lst-raster", default=None, help="optional Landsat LST GeoTIFF")
    ap.add_argument("--cache", default=".osmnx_cache")
    ap.add_argument("--out", default="data/graph.json.gz")
    ap.add_argument("--bucket", default=os.environ.get("GRAPH_BUCKET", ""))
    ap.add_argument("--key", default=os.environ.get("GRAPH_KEY", "graph.json.gz"))
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    graph = build(tuple(args.centre), args.radius, args.date, args.tz,
                  args.lst_raster, args.cache, args.pilot)
    size = write_graph(graph, args.out)
    log(f"wrote {args.out} ({size/1024:.1f} KiB uncompressed) in {time.time()-t0:.0f} s")
    summary(graph)

    if not args.no_upload and args.bucket:
        upload(args.out, args.bucket, args.key)
    else:
        print("\nUpload manually with:")
        print(f"  aws s3 cp {args.out} s3://<GRAPH_BUCKET>/{args.key}")


if __name__ == "__main__":
    main()
