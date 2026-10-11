#!/usr/bin/env python3
"""PHASE 0 — Synthetic sample street graph for Chhaon.

Generates a plausible ~15x15 street grid around the pilot centre, with every
edge attribute that the real `build_graph.py` will eventually produce, in the
*exact same* `graph.json.gz` format. This lets the Lambda and the frontend
integrate immediately, before any OSM data is downloaded.

The numbers here are synthetic and explicitly marked as such in the metadata
(`sources` and `methods`). Never present them as measured.

Usage:
    python scripts/make_sample_graph.py                 # writes data/graph.json.gz
    python scripts/make_sample_graph.py --out /tmp/g.json.gz --seed 7
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
from datetime import datetime, timezone

# --- Pilot area (one area only) ---------------------------------------------
PILOT = "Connaught Place, Delhi (SAMPLE)"
CENTRE = (28.6314, 77.2197)          # (lat, lon)
RADIUS_M = 1000
TIMEZONE = "Asia/Kolkata"
HOURS = list(range(8, 19))            # 8..18 inclusive
DATE_USED = "2026-05-15"              # representative hot day (synthetic here)
GRID_N = 15                           # 15x15 nodes

R_EARTH = 6371000.0
DEFAULT_BUILDING_HEIGHT_M = 9.0
HIGHWAYS = ["residential", "residential", "residential", "tertiary",
            "secondary", "primary", "footway", "pedestrian"]

# Road-class pollution proxy (identical to the real builder's table).
POLLUTION_BY_HIGHWAY = {
    "motorway": 1.0, "trunk": 1.0, "primary": 0.85, "secondary": 0.7,
    "tertiary": 0.5, "residential": 0.25, "footway": 0.1, "path": 0.1,
    "pedestrian": 0.1,
}


def solar_intensity_by_hour(lat: float, date_used: str, hours: list[int]) -> list[float]:
    """Solar altitude (NOAA approximation) -> sin(altitude) clipped to 0..1.

    Synthesised locally so the sample script needs no heavy dependencies. The
    real builder uses pvlib/astral for the same quantity.
    """
    month, day = int(date_used[5:7]), int(date_used[8:10])
    # Simplified declination (Cooper's equation) — good enough for a proxy.
    dec = math.radians(23.45 * math.sin(math.radians(360 / 365 * (284 + day_of_year(month, day)))))
    lat_r = math.radians(lat)
    out = []
    for h in hours:
        # Local solar noon ~ 12:20 IST; use civil hour as a close proxy.
        hour_angle = math.radians(15.0 * (h + 0.3 - 12.0))
        alt = math.asin(
            math.sin(lat_r) * math.sin(dec)
            + math.cos(lat_r) * math.cos(dec) * math.cos(hour_angle)
        )
        out.append(round(max(0.0, min(1.0, math.sin(alt))), 4))
    return out


def day_of_year(month: int, day: int) -> int:
    cum = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    return cum[month - 1] + day


def metres_to_deg(m: float, lat: float) -> tuple[float, float]:
    dlat = m / 111320.0
    dlon = m / (111320.0 * math.cos(math.radians(lat)))
    return dlat, dlon


def build(seed: int) -> dict:
    rng = random.Random(seed)
    lat0, lon0 = CENTRE
    dlat_1m, dlon_1m = metres_to_deg(1.0, lat0)

    # Grid spans 2*RADIUS across, inclusive of both ends.
    n = GRID_N
    step_m = (2 * RADIUS_M) / (n - 1)
    origin_lat = lat0 - RADIUS_M * dlat_1m
    origin_lon = lon0 - RADIUS_M * dlon_1m

    nodes = []
    idx = {}
    for r in range(n):
        for c in range(n):
            i = r * n + c
            idx[(r, c)] = i
            nodes.append({
                "id": i,
                "lat": round(origin_lat + r * step_m * dlat_1m, 7),
                "lon": round(origin_lon + c * step_m * dlon_1m, 7),
            })

    def node_ll(i):
        return nodes[i]["lat"], nodes[i]["lon"]

    # Per-edge coherent random "hotness" so nearby streets behave similarly.
    hot = {}

    def hotness(i):
        if i not in hot:
            hot[i] = rng.random()
        return hot[i]

    def building_shade_profile(u, v, highway):
        """Plausible *building* shade per hour (tree shade added separately).

        A low sun casts longer shadows, so building shade is larger when the
        solar intensity is small — the ends of the day.
        """
        base = 0.10 + 0.35 * hotness(u)
        profile = []
        for h in HOURS:
            solar = solar_intensity_by_hour(lat0, DATE_USED, [h])[0]
            low_sun = 1.0 - solar
            building = min(0.85, base + 0.45 * low_sun * (0.6 + 0.4 * hotness(v)))
            if highway in ("footway", "pedestrian"):
                building *= 0.7
            profile.append(round(building, 4))
        return profile

    edges = []
    for r in range(n):
        for c in range(n):
            u = idx[(r, c)]
            for dr, dc in ((0, 1), (1, 0)):
                rr, cc = r + dr, c + dc
                if rr >= n or cc >= n:
                    continue
                v = idx[(rr, cc)]
                lat_u, lon_u = node_ll(u)
                lat_v, lon_v = node_ll(v)
                length = math.hypot(
                    (lat_v - lat_u) * 111320.0,
                    (lon_v - lon_u) * 111320.0 * math.cos(math.radians(lat0)),
                )
                highway = rng.choice(HIGHWAYS)
                building_profile = building_shade_profile(u, v, highway)
                tree_shade = round(min(0.5, 0.05 + 0.3 * rng.random()), 4)
                # Combine exactly like the real builder: 1-(1-building)(1-tree)
                shade_by_hour = [
                    round(1.0 - (1.0 - b) * (1.0 - tree_shade), 4)
                    for b in building_profile
                ]
                heat = round(min(1.0, 0.6 * (0.4 + 0.6 * hotness(u)) + 0.4 * (1 - 0.25 * rng.random())), 4)
                pollution = round(min(1.0, POLLUTION_BY_HIGHWAY.get(highway, 0.3)
                                      + (0.1 if highway in ("primary", "trunk", "motorway") else 0.0)), 4)
                # Straight geometry with a small bend at the midpoint.
                mid_lat = (lat_u + lat_v) / 2 + rng.uniform(-0.00008, 0.00008)
                mid_lon = (lon_u + lon_v) / 2 + rng.uniform(-0.00008, 0.00008)
                geometry = [
                    [round(lon_u, 5), round(lat_u, 5)],
                    [round(mid_lon, 5), round(mid_lat, 5)],
                    [round(lon_v, 5), round(lat_v, 5)],
                ]
                edges.append({
                    "u": u, "v": v,
                    "length_m": round(length, 2),
                    "highway": highway,
                    "shade_by_hour": shade_by_hour,
                    "tree_shade": tree_shade,
                    "building_shade_by_hour": building_profile,
                    "heat": heat,
                    "pollution": pollution,
                    "geometry": geometry,
                })

    lats = [nd["lat"] for nd in nodes]
    lons = [nd["lon"] for nd in nodes]
    meta = {
        "pilot": PILOT,
        "centre": [lat0, lon0],
        "bbox": [min(lats), min(lons), max(lats), max(lons)],
        "radius_m": RADIUS_M,
        "hours": HOURS,
        "date_used": DATE_USED,
        "timezone": TIMEZONE,
        "built": datetime.now(timezone.utc).isoformat(),
        "is_sample": True,
        "solar_intensity_by_hour": solar_intensity_by_hour(lat0, DATE_USED, HOURS),
        "walker_speeds_kmh": {"adult": 5.0, "vendor": 4.5, "elderly": 3.5, "child": 3.5},
        "defaults": {"building_height_m": DEFAULT_BUILDING_HEIGHT_M,
                     "levels_to_metres": 3.0},
        "sources": ["SYNTHETIC SAMPLE — not real OpenStreetMap data"],
        "methods": {
            "shade": "synthetic random sample (shade = 1-(1-building)(1-tree))",
            "heat": "synthetic random sample (0.6*built + 0.4*(1-green))",
            "pollution": "synthetic random sample (road-class proxy)",
            "solar_intensity": "synthetic sin(altitude), NOAA approximation",
        },
    }
    return {"meta": meta, "nodes": nodes, "edges": edges}


def write_gz(obj: dict, path: str) -> int:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(raw)
    return len(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic Chhaon sample graph.")
    ap.add_argument("--out", default="data/graph.json.gz", help="output .json.gz path")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    graph = build(args.seed)
    import os
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    size = write_gz(graph, args.out)

    meta, nodes, edges = graph["meta"], graph["nodes"], graph["edges"]
    mean_shade = [
        sum(e["shade_by_hour"][i] for e in edges) / len(edges)
        for i in range(len(meta["hours"]))
    ]
    print(f"Wrote {args.out}  ({size/1024:.1f} KiB uncompressed)")
    print(f"  pilot      : {meta['pilot']}")
    print(f"  nodes      : {len(nodes)}")
    print(f"  edges      : {len(edges)}")
    print(f"  bbox       : {meta['bbox']}")
    print("  mean shade : " + ", ".join(
        f"{h}:{v:.2f}" for h, v in zip(meta["hours"], mean_shade)))
    print("  methods    : shade=%s heat=%s" % (meta["methods"]["shade"], meta["methods"]["heat"]))


if __name__ == "__main__":
    main()
