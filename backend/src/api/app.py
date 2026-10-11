#!/usr/bin/env python3
"""CHHAON routing Lambda.

Single Lambda behind an API Gateway HTTP API. Loads one pre-scored street
graph (built offline by `scripts/build_graph.py`, or the synthetic sample from
`scripts/make_sample_graph.py`) from S3, runs three shortest-path searches with
different cost weightings, and returns the routes plus a short bilingual
recommendation from Amazon Bedrock (with a deterministic template fallback).

Dependencies kept intentionally light: numpy + networkx only (+ boto3, which the
Lambda runtime provides). No osmnx / geopandas / shapely here.

Everything returned is an ESTIMATE, based on map data and simple models.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import networkx as nx

try:  # boto3 is available in Lambda; locally it may be absent for graph-only tests
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # pragma: no cover
    boto3 = None
    BotoConfig = None


# ---------------------------------------------------------------------------
# CONFIG — one place for every tunable
# ---------------------------------------------------------------------------
CONFIG = {
    # "equivalent seconds per metre" for (sun exposure, heat, pollution)
    "walker_weights": {
        "adult":   {"a": 0.30, "b": 0.15, "c": 0.10},
        "vendor":  {"a": 0.20, "b": 0.10, "c": 0.10},
        "elderly": {"a": 0.80, "b": 0.40, "c": 0.25},
        "child":   {"a": 0.80, "b": 0.40, "c": 0.30},
    },
    "walker_speeds_kmh": {"adult": 5.0, "vendor": 4.5, "elderly": 3.5, "child": 3.5},
    # Multiplier applied to the walker weights for the balanced / coolest routes.
    "route_multiplier": {"fastest": 0.0, "balanced": 0.5, "coolest": 1.5},
    # exposure_index = 100 * mean(0.6*sun + 0.25*heat + 0.15*pollution)
    "exposure_weights": {"sun": 0.6, "heat": 0.25, "pollution": 0.15},
    "bbox_margin_m": 300,     # request points may sit this far outside the bbox
    "max_snap_m": 300,        # nearest-node distance limit -> "outside pilot area"
    "bedrock_timeout_s": 6,
    "bedrock_max_retries": 1,
    "walkers": ["elderly", "child", "vendor", "adult"],
    "disclaimer": ("Estimated values for a pilot area, based on map data and "
                   "simple models. Not measured conditions."),
    "default_hour": 13,
}

R_EARTH = 6371000.0

# Module globals — populated once on cold start, reused by warm invocations.
_GRAPH = None          # networkx.Graph
_META = None           # dict
_COORDS = None         # numpy array (N,2) of [lat, lon]
_IDS = None            # node id -> row index
_EDGE_DATA = None      # (u,v) canonical -> edge attribute dict
_GRAPH_LOADED_AT = None

_HOURS_INDEX = None    # available hours list
_SOLAR = None          # numpy array of solar intensity per available hour
_EDGE_LEN = None       # numpy array of edge lengths aligned with graph edges
_EDGE_SHADE = None     # numpy array (E, H) of shade by available hour
_EDGE_HEAT = None      # numpy array (E,)
_EDGE_POLL = None      # numpy array (E,)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Graph loading / caching
# ---------------------------------------------------------------------------
def _download_graph_bytes() -> bytes:
    """Return the raw gzipped graph bytes from a local path or from S3."""
    local = os.environ.get("GRAPH_LOCAL_PATH")
    if local and os.path.exists(local):
        with open(local, "rb") as fh:
            return fh.read()

    if boto3 is None:
        raise ApiError(503, "Graph storage client unavailable")
    bucket = os.environ.get("GRAPH_BUCKET")
    key = os.environ.get("GRAPH_KEY", "graph.json.gz")
    if not bucket:
        raise ApiError(503, "GRAPH_BUCKET is not configured")

    s3 = boto3.client("s3", config=BotoConfig(
        connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def _parse_graph(raw: bytes) -> dict:
    if raw[:2] == b"\x1f\x8b":            # gzip magic
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _load_graph(force: bool = False) -> tuple:
    """Load and cache the graph. Warm invocations must not reload."""
    global _GRAPH, _META, _COORDS, _IDS, _EDGE_DATA, _GRAPH_LOADED_AT
    global _HOURS_INDEX, _SOLAR, _EDGE_LEN, _EDGE_SHADE, _EDGE_HEAT, _EDGE_POLL

    if _GRAPH is not None and not force:
        return _GRAPH, _META

    raw = _download_graph_bytes()
    data = _parse_graph(raw)

    meta = data["meta"]
    nodes = data["nodes"]
    edges = data["edges"]
    hours = list(meta["hours"])

    # --- nodes -> numpy coordinate array -----------------------------------
    ids = {}
    coords = np.empty((len(nodes), 2), dtype=np.float64)
    for i, nd in enumerate(nodes):
        ids[nd["id"]] = i
        coords[i, 0] = nd["lat"]
        coords[i, 1] = nd["lon"]

    # --- edges -> networkx, merging parallel edges (keep shortest) ----------
    G = nx.Graph()
    G.add_nodes_from(range(len(nodes)))
    edge_data = {}
    for e in edges:
        ui, vi = ids.get(e["u"]), ids.get(e["v"])
        if ui is None or vi is None or ui == vi:
            continue
        key = (ui, vi) if ui < vi else (vi, ui)
        existing = edge_data.get(key)
        if existing is None or e["length_m"] < existing["length_m"]:
            edge_data[key] = e

    edge_list = list(edge_data.items())
    E = len(edge_list)
    H = len(hours)
    edge_len = np.empty(E, dtype=np.float64)
    edge_shade = np.empty((E, H), dtype=np.float64)
    edge_heat = np.empty(E, dtype=np.float64)
    edge_poll = np.empty(E, dtype=np.float64)
    for j, ((ui, vi), e) in enumerate(edge_list):
        L = float(e["length_m"])
        sh = e.get("shade_by_hour") or [0.0] * H
        if len(sh) < H:
            sh = list(sh) + [sh[-1] if sh else 0.0] * (H - len(sh))
        heat = float(e.get("heat", 0.0))
        poll = float(e.get("pollution", 0.0))
        G.add_edge(ui, vi, len=L, shade=sh, heat=heat, poll=poll)
        edge_len[j] = L
        edge_shade[j, :] = sh[:H]
        edge_heat[j] = heat
        edge_poll[j] = poll

    _GRAPH, _META, _COORDS, _IDS, _EDGE_DATA = G, meta, coords, ids, edge_data
    _HOURS_INDEX = hours
    _SOLAR = np.asarray(meta.get("solar_intensity_by_hour", [0.0] * H), dtype=np.float64)
    _EDGE_LEN, _EDGE_SHADE, _EDGE_HEAT, _EDGE_POLL = edge_len, edge_shade, edge_heat, edge_poll
    _GRAPH_LOADED_AT = datetime.now(timezone.utc).isoformat()
    return G, meta


def _load_state():
    """Ensure the graph is loaded and return the flat caches used by routing."""
    _load_graph()
    return {
        "G": _GRAPH, "meta": _META, "coords": _COORDS,
        "edge_data": _EDGE_DATA, "hours": _HOURS_INDEX,
    }


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------
def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ApiError(400, f"{name} must be a number")
    if math.isnan(v) or math.isinf(v):
        raise ApiError(400, f"{name} must be a finite number")
    return float(v)


def _hour_index(meta, hour):
    """Clamp a requested hour to the nearest available precomputed hour."""
    hours = list(meta["hours"])
    hi = int(round(hour))
    nearest = min(hours, key=lambda h: abs(h - hi))
    return hours.index(nearest), nearest


def snap_to_node(coords, lat, lon):
    """Nearest node with an equirectangular distance. Returns (index, metres)."""
    lat0 = math.radians(lat)
    dlat = np.radians(coords[:, 0] - lat)
    dlon = np.radians(coords[:, 1] - lon) * math.cos(lat0)
    dist = R_EARTH * np.sqrt(dlat * dlat + dlon * dlon)
    i = int(np.argmin(dist))
    return i, float(dist[i])


def _inside_bbox(meta, lat, lon):
    minlat, minlon, maxlat, maxlon = meta["bbox"]
    lat_pad = CONFIG["bbox_margin_m"] / 111320.0
    lon_pad = CONFIG["bbox_margin_m"] / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    return (minlat - lat_pad) <= lat <= (maxlat + lat_pad) and \
           (minlon - lon_pad) <= lon <= (maxlon + lon_pad)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def _edge_lookup(G, coords, edge_data, u, v):
    key = (u, v) if u < v else (v, u)
    return edge_data.get(key)


def _make_weight(speed_kmh, a, b, c, si, hi_):
    """Return a networkx weight callable for one (walker, route) combination.

    cost = time_s + length_m * (a*sun_exposure + b*heat + c*pollution)
    """
    sec_per_metre = 3.6 / speed_kmh

    def w(u, v, d):
        L = d["len"]
        sun = (1.0 - d["shade"][hi_]) * si
        return L * sec_per_metre + L * (a * sun + b * d["heat"] + c * d["poll"])
    return w


def _path_stats(path, edge_data, hi_, si, speed_kmh):
    total_len = 0.0
    shade_len = 0.0
    sun_len = 0.0
    heat_len = 0.0
    poll_len = 0.0
    coords_out = []
    for a, b in zip(path[:-1], path[1:]):
        e = _edge_lookup(None, None, edge_data, a, b)
        if e is None:
            continue
        L = float(e["length_m"])
        shade = float(e["shade_by_hour"][hi_])
        heat = float(e.get("heat", 0.0))
        poll = float(e.get("pollution", 0.0))
        sun = (1.0 - shade) * si
        total_len += L
        shade_len += L * shade
        sun_len += L * sun
        heat_len += L * heat
        poll_len += L * poll
        coords_out.extend(_oriented_geometry(e, a, b))
    if total_len <= 0:
        return None
    total_s = total_len / (speed_kmh * 1000.0 / 3600.0)
    shade_frac = shade_len / total_len
    sun_mean = sun_len / total_len
    exposure = (CONFIG["exposure_weights"]["sun"] * sun_mean
                + CONFIG["exposure_weights"]["heat"] * (heat_len / total_len)
                + CONFIG["exposure_weights"]["pollution"] * (poll_len / total_len))
    return {
        "minutes": round(total_s / 60.0, 1),
        "distance_km": round(total_len / 1000.0, 2),
        "shade_percent": round(shade_frac * 100.0, 1),
        "exposure_index": round(min(100.0, max(0.0, exposure * 100.0)), 1),
        "sun_mean": sun_mean,
        "coordinates": _dedupe(coords_out),
    }


def _oriented_geometry(edge, a, b):
    """Edge geometry as [lon,lat] pairs, oriented from node a to node b."""
    geom = edge.get("geometry")
    if not geom:
        return []
    is_fwd = edge["u"] == a and edge["v"] == b
    if is_fwd:
        return [[float(p[0]), float(p[1])] for p in geom]
    # Either stored v->u, or traversed against the stored direction.
    return [[float(p[0]), float(p[1])] for p in reversed(geom)]


def _dedupe(points):
    out = []
    for p in points:
        if not out or out[-1] != p:
            out.append(p)
    return out


def _route_path(G, edge_data, src, dst, speed_kmh, a, b, c, si, hi_):
    w = _make_weight(speed_kmh, a, b, c, si, hi_)
    try:
        return nx.shortest_path(G, src, dst, weight=w, method="dijkstra")
    except nx.NetworkXNoPath:
        raise ApiError(404, "No walking route found between those points")


def _sun_exposure_reduction(base_sun, other_sun):
    if base_sun <= 0:
        return 0.0
    return round(max(0.0, (base_sun - other_sun) / base_sun * 100.0), 1)


# ---------------------------------------------------------------------------
# Bedrock advice (with deterministic template fallback)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are the Chhaon walking advisor. Use ONLY the numbers provided in the "
    "user message. Never invent facts, never claim measured conditions, and "
    "never give medical advice. Write 2 to 3 short sentences: name the "
    "recommended route, the time trade-off, and one practical tip (for example "
    "carry water or rest in shade). Return STRICT JSON only, with exactly the "
    'shape {"en": "...", "hi": "..."} and no other text.'
)


def _template_advice(walker, hour, routes):
    by = {r["type"]: r for r in routes}
    fast, cool = by.get("fastest"), by.get("coolest")
    if not fast or not cool:
        return {"en": "Routes could not be compared.", "hi": "मार्गों की तुलना नहीं हो सकी।"}
    extra = round(cool["minutes"] - fast["minutes"], 1)
    en = (f"For the {walker} walker at about {hour}:00, the coolest route takes "
          f"{cool['minutes']} minutes and is {cool['shade_percent']}% shaded, "
          f"about {extra} minutes longer than the fastest route. Prefer the "
          f"coolest route if the extra time is fine, carry water, and rest in "
          f"shade if you feel unwell.")
    # HINDI TEMPLATE — REVIEW BY A NATIVE SPEAKER BEFORE THE SUBMISSION.
    hi = (f"{walker} धावक के लिए लगभग {hour}:00 बजे, सबसे ठंडा मार्ग "
          f"{cool['minutes']} मिनट का है और {cool['shade_percent']}% छाया में है, "
          f"जो सबसे तेज़ मार्ग से लगभग {extra} मिनट अधिक है। समय हो तो सबसे ठंडा "
          f"मार्ग चुनें, पानी साथ रखें, और अस्वस्थ लगे तो छाया में आराम करें।")
    return {"en": en, "hi": hi}


def _parse_advice_json(text):
    """Defensively parse the model output into {"en","hi"}."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except Exception:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("en"), str) and isinstance(obj.get("hi"), str):
        return {"en": obj["en"].strip(), "hi": obj["hi"].strip()}
    return None


def _bedrock_advice(walker, hour, routes):
    """Call Bedrock Converse. Returns ({"en","hi"}, "bedrock") or (None, None)."""
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id or boto3 is None:
        return None, None
    client = boto3.client(
        "bedrock-runtime",
        config=BotoConfig(
            connect_timeout=CONFIG["bedrock_timeout_s"],
            read_timeout=CONFIG["bedrock_timeout_s"],
            retries={"max_attempts": CONFIG["bedrock_max_retries"]},
        ),
    )
    compact = [{k: r[k] for k in
                ("type", "minutes", "distance_km", "shade_percent", "exposure_index")}
               for r in routes]
    user = json.dumps({"walker": walker, "hour": hour, "routes": compact})
    last_err = None
    for _ in range(CONFIG["bedrock_max_retries"] + 1):
        try:
            resp = client.converse(
                modelId=model_id,
                system=[{"text": _SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": 400, "temperature": 0.2},
            )
            text = resp["output"]["message"]["content"][0]["text"]
            parsed = _parse_advice_json(text)
            if parsed:
                return parsed, "bedrock"
            last_err = "unparseable"
        except Exception as exc:  # noqa: BLE001 - any Bedrock failure -> template
            last_err = str(exc)
    print(f"[chhaon] bedrock advice failed, using template: {last_err}")
    return None, None


def get_advice(walker, hour, routes):
    advice, source = _bedrock_advice(walker, hour, routes)
    if advice:
        return advice, source
    return _template_advice(walker, hour, routes), "template"


# ---------------------------------------------------------------------------
# Request logging (optional, failure-tolerant)
# ---------------------------------------------------------------------------
def _log_request(request_id, walker, hour_used, routes, start, end):
    table = os.environ.get("TABLE_NAME")
    if not table or boto3 is None:
        return
    try:
        ddb = boto3.client("dynamodb", config=BotoConfig(
            connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}))
        now = int(time.time())
        item = {
            "request_id": {"S": request_id},
            "ts": {"N": str(now)},
            "walker": {"S": walker},
            "hour_used": {"N": str(hour_used)},
            "routes": {"S": json.dumps([
                {"type": r["type"], "minutes": r["minutes"],
                 "distance_km": r["distance_km"], "shade_percent": r["shade_percent"],
                 "exposure_index": r["exposure_index"]} for r in routes])},
            # Rounded to 3 decimals so exact locations are not stored.
            "start": {"S": f"{round(start['lat'],3)},{round(start['lon'],3)}"},
            "end": {"S": f"{round(end['lat'],3)},{round(end['lon'],3)}"},
            "expires_at": {"N": str(now + 7 * 24 * 3600)},  # 7-day TTL
        }
        ddb.put_item(TableName=table, Item=item)
    except Exception as exc:  # noqa: BLE001 - logging must never break a response
        print(f"[chhaon] logging skipped: {exc}")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _cors():
    return {
        "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Content-Type": "application/json",
    }


def _resp(status, body):
    return {"statusCode": status, "headers": _cors(), "body": json.dumps(body)}


def _handle_health():
    try:
        _load_graph()
        ok = True
    except Exception:
        ok = False
    return _resp(200, {"status": "ok" if ok else "degraded", "graph_loaded": ok})


def _handle_meta():
    _, meta = _load_graph()
    return _resp(200, {
        "pilot": meta["pilot"],
        "centre": meta["centre"],
        "bbox": meta["bbox"],
        "available_hours": meta["hours"],
        "walker_types": CONFIG["walkers"],
        "disclaimer": CONFIG["disclaimer"],
        "is_sample": bool(meta.get("is_sample", False)),
        "graph_built": meta.get("built"),
        "methods": meta.get("methods", {}),
    })


def _handle_route(body):
    st = _load_state()
    meta = st["meta"]
    coords = st["coords"]

    if not isinstance(body, dict):
        raise ApiError(400, "Request body must be a JSON object")
    start = body.get("start")
    end = body.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ApiError(400, "start and end are required objects with lat and lon")

    s_lat = _num(start.get("lat"), "start.lat")
    s_lon = _num(start.get("lon"), "start.lon")
    e_lat = _num(end.get("lat"), "end.lat")
    e_lon = _num(end.get("lon"), "end.lon")

    hour_in = body.get("hour", CONFIG["default_hour"])
    if isinstance(hour_in, bool) or not isinstance(hour_in, (int, float)):
        raise ApiError(400, "hour must be a number between 0 and 23")
    if hour_in < 0 or hour_in > 23:
        raise ApiError(400, "hour must be between 0 and 23")

    walker = body.get("walker", "adult")
    if walker not in CONFIG["walkers"]:
        raise ApiError(400, "walker must be one of: " + ", ".join(CONFIG["walkers"]))

    for name, la, lo in (("start", s_lat, s_lon), ("end", e_lat, e_lon)):
        if not _inside_bbox(meta, la, lo):
            raise ApiError(400, f"{name} is outside the pilot area")

    src, d_s = snap_to_node(coords, s_lat, s_lon)
    dst, d_e = snap_to_node(coords, e_lat, e_lon)
    if d_s > CONFIG["max_snap_m"] or d_e > CONFIG["max_snap_m"]:
        raise ApiError(400, "outside pilot area")
    if src == dst:
        raise ApiError(400, "start and end snap to the same point")

    hi_, hour_used = _hour_index(meta, hour_in)
    si = float(_SOLAR[hi_])
    speed = CONFIG["walker_speeds_kmh"][walker]
    base = CONFIG["walker_weights"][walker]

    G = st["G"]
    edge_data = st["edge_data"]

    routes = []
    sun_by_type = {}
    path_nodes = {}
    for rtype in ("fastest", "balanced", "coolest"):
        mult = CONFIG["route_multiplier"][rtype]
        path = _route_path(G, edge_data, src, dst, speed,
                           base["a"] * mult, base["b"] * mult, base["c"] * mult, si, hi_)
        stats = _path_stats(path, edge_data, hi_, si, speed)
        if stats is None:
            raise ApiError(404, "No walking route found between those points")
        node_key = tuple(path)
        same_as = None
        for other_type in ("fastest", "balanced", "coolest"):
            if other_type != rtype and path_nodes.get(other_type) == node_key:
                same_as = other_type
                break
        path_nodes[rtype] = node_key
        sun_by_type[rtype] = stats["sun_mean"]
        route = {
            "type": rtype,
            "minutes": stats["minutes"],
            "distance_km": stats["distance_km"],
            "shade_percent": stats["shade_percent"],
            "exposure_index": stats["exposure_index"],
            "geojson": {"type": "LineString", "coordinates": stats["coordinates"]},
        }
        if same_as:
            route["same_as"] = same_as
        routes.append(route)

    for r in routes:
        if r["type"] != "fastest":
            r["sun_exposure_reduction_vs_fastest_percent"] = _sun_exposure_reduction(
                sun_by_type["fastest"], sun_by_type[r["type"]])

    advice, source = get_advice(walker, hour_used, routes)

    request_id = str(uuid.uuid4())
    _log_request(request_id, walker, hour_used,
                 routes, {"lat": s_lat, "lon": s_lon}, {"lat": e_lat, "lon": e_lon})

    return _resp(200, {
        "hour_used": hour_used,
        "walker": walker,
        "routes": routes,
        "advice": advice,
        "advice_source": source,
        "disclaimer": CONFIG["disclaimer"],
        "graph_meta": {
            "pilot": meta["pilot"],
            "built": meta.get("built"),
            "methods": meta.get("methods", {}),
        },
    })


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context=None):  # noqa: ANN001
    try:
        method = (event.get("requestContext", {}).get("http", {}).get("method")
                  or event.get("httpMethod") or "GET")
        path = event.get("rawPath") or event.get("path") or "/"

        if method == "OPTIONS":
            return _resp(204, {})
        if path.endswith("/health"):
            return _handle_health()
        if path.endswith("/meta"):
            return _handle_meta()
        if path.endswith("/route"):
            if method != "POST":
                raise ApiError(405, "Use POST for /route")
            raw = event.get("body")
            if event.get("isBase64Encoded") and raw:
                import base64
                raw = base64.b64decode(raw).decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                raise ApiError(400, "Request body is not valid JSON")
            return _handle_route(body)

        return _resp(404, {"error": "Not found"})
    except ApiError as exc:
        return _resp(exc.status, {"error": exc.message})
    except Exception as exc:  # noqa: BLE001
        print(f"[chhaon] unhandled error: {exc}")
        return _resp(500, {"error": "Internal server error"})
