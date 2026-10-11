"""Shared pytest fixtures for the Chhaon API tests.

The tests run entirely offline against a generated graph; no AWS calls are made.
"""
from __future__ import annotations

import gzip
import importlib
import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
API_DIR = os.path.join(ROOT, "src", "api")
SCRIPTS = os.path.join(ROOT, "scripts")
for p in (API_DIR, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def sample_graph_path(tmp_path_factory):
    """A generated 15x15 synthetic grid graph (Phase 0 output)."""
    from make_sample_graph import build, write_gz
    path = tmp_path_factory.mktemp("sample") / "graph.json.gz"
    write_gz(build(seed=42), str(path))
    return str(path)


def _write_gz(obj, path):
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb") as fh:
        fh.write(raw)


# Deterministic graph: three parallel corridors between A and D:
#   fastest corridor  (P): short, hot, sunny, polluted
#   balanced corridor (Q): medium
#   coolest corridor  (R): long, shady, cool, clean
# Chosen so that, for an adult at hour 13, the three weightings pick P, Q, R.
def _test_graph():
    nodes = [
        {"id": 0, "lat": 28.6310, "lon": 77.2190},   # A  (start)
        {"id": 1, "lat": 28.6318, "lon": 77.2210},   # P
        {"id": 2, "lat": 28.6310, "lon": 77.2210},   # Q
        {"id": 3, "lat": 28.6302, "lon": 77.2210},   # R
        {"id": 4, "lat": 28.6310, "lon": 77.2230},   # D  (end)
    ]
    hours = list(range(8, 19))
    si = [0.9] * len(hours)

    def edge(u, v, length, tree_shade, building, heat, poll, geom):
        shade = [round(1 - (1 - b) * (1 - tree_shade), 4) for b in building]
        return {
            "u": u, "v": v, "length_m": length, "highway": "residential",
            "shade_by_hour": shade, "tree_shade": tree_shade,
            "building_shade_by_hour": building, "heat": heat,
            "pollution": poll, "geometry": geom,
        }

    P = [[77.2190, 28.6310], [77.2210, 28.6318], [77.2230, 28.6310]]
    Q = [[77.2190, 28.6310], [77.2210, 28.6310], [77.2230, 28.6310]]
    R = [[77.2190, 28.6310], [77.2210, 28.6302], [77.2230, 28.6310]]
    edges = [
        edge(0, 1, 150, 0.0, [0.0] * len(hours), 1.0, 1.0, [P[0], P[1]]),
        edge(1, 4, 150, 0.0, [0.0] * len(hours), 1.0, 1.0, [P[1], P[2]]),
        edge(0, 2, 170, 0.5, [0.0] * len(hours), 0.5, 0.5, [Q[0], Q[1]]),
        edge(2, 4, 170, 0.5, [0.0] * len(hours), 0.5, 0.5, [Q[1], Q[2]]),
        edge(0, 3, 200, 0.9, [0.0] * len(hours), 0.1, 0.1, [R[0], R[1]]),
        edge(3, 4, 200, 0.9, [0.0] * len(hours), 0.1, 0.1, [R[1], R[2]]),
    ]
    lats = [n["lat"] for n in nodes]
    lons = [n["lon"] for n in nodes]
    meta = {
        "pilot": "TEST GRID",
        "centre": [28.6310, 77.2210],
        "bbox": [min(lats), min(lons), max(lats), max(lons)],
        "radius_m": 500,
        "hours": hours,
        "date_used": "2026-05-15",
        "timezone": "Asia/Kolkata",
        "built": "2026-05-15T00:00:00+00:00",
        "is_sample": True,
        "solar_intensity_by_hour": si,
        "walker_speeds_kmh": {"adult": 5.0, "vendor": 4.5, "elderly": 3.5, "child": 3.5},
        "defaults": {}, "sources": ["test"],
        "methods": {"shade": "test", "heat": "test"},
    }
    return {"meta": meta, "nodes": nodes, "edges": edges}


@pytest.fixture(scope="session")
def test_graph_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("det") / "graph.json.gz"
    _write_gz(_test_graph(), str(path))
    return str(path)


@pytest.fixture
def load_app(monkeypatch):
    """Return a loader that reloads app.py pointed at a given graph file."""
    def _load(graph_path, bedrock_model=None, table_name=None):
        monkeypatch.setenv("GRAPH_LOCAL_PATH", graph_path)
        monkeypatch.delenv("GRAPH_BUCKET", raising=False)
        if bedrock_model is None:
            monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        else:
            monkeypatch.setenv("BEDROCK_MODEL_ID", bedrock_model)
        if table_name is None:
            monkeypatch.delenv("TABLE_NAME", raising=False)
        else:
            monkeypatch.setenv("TABLE_NAME", table_name)
        import app
        return importlib.reload(app)
    return _load


def get(app, path, method="GET", body=None):
    import json as _json
    event = {
        "requestContext": {"http": {"method": method}},
        "rawPath": path, "path": path,
        "body": _json.dumps(body) if body is not None else None,
    }
    res = app.handler(event)
    return res["statusCode"], json.loads(res["body"]), res["headers"]
