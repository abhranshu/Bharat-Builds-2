"""The three cost weightings must produce different routes."""
import json

from conftest import get


def _geom_key(route):
    return json.dumps(route["geojson"]["coordinates"])


def _route(app, start, end, hour=13, walker="adult"):
    status, body, _ = get(app, "/route", "POST", {
        "start": start, "end": end, "hour": hour, "walker": walker})
    assert status == 200, body
    return body


def test_three_weightings_differ_on_deterministic_graph(load_app, test_graph_path):
    app = load_app(test_graph_path)
    body = _route(app,
                  {"lat": 28.6310, "lon": 77.2190},
                  {"lat": 28.6310, "lon": 77.2230})
    paths = {r["type"]: _geom_key(r) for r in body["routes"]}
    assert len(paths) == 3
    assert paths["fastest"] != paths["balanced"]
    assert paths["balanced"] != paths["coolest"]
    assert paths["fastest"] != paths["coolest"]


def test_coolest_is_shadier_and_shorter_on_exposure(load_app, test_graph_path):
    app = load_app(test_graph_path)
    body = _route(app,
                  {"lat": 28.6310, "lon": 77.2190},
                  {"lat": 28.6310, "lon": 77.2230})
    by = {r["type"]: r for r in body["routes"]}
    assert by["coolest"]["shade_percent"] > by["fastest"]["shade_percent"]
    assert by["coolest"]["exposure_index"] < by["fastest"]["exposure_index"]
    assert by["coolest"]["distance_km"] > by["fastest"]["distance_km"]


def test_reduction_vs_fastest_present(load_app, test_graph_path):
    app = load_app(test_graph_path)
    body = _route(app,
                  {"lat": 28.6310, "lon": 77.2190},
                  {"lat": 28.6310, "lon": 77.2230})
    by = {r["type"]: r for r in body["routes"]}
    assert "sun_exposure_reduction_vs_fastest_percent" not in by["fastest"]
    assert by["coolest"]["sun_exposure_reduction_vs_fastest_percent"] > 0


def test_weightings_can_differ_on_sample_graph(load_app, sample_graph_path):
    """Across many OD pairs on the random sample, at least one yields two
    different routes for the different weightings."""
    app = load_app(sample_graph_path)
    app._load_graph()
    coords = app._COORDS
    different = 0
    pairs = [(0, len(coords) - 1), (5, 200), (30, 210), (100, 20), (60, 180)]
    for a, b in pairs:
        sa = {"lat": float(coords[a][0]), "lon": float(coords[a][1])}
        sb = {"lat": float(coords[b][0]), "lon": float(coords[b][1])}
        body = _route(app, sa, sb)
        paths = {r["type"]: _geom_key(r) for r in body["routes"]}
        if len(set(paths.values())) > 1:
            different += 1
    assert different >= 1


def test_same_as_marked_when_routes_identical(load_app, sample_graph_path):
    """When two routes coincide the response marks same_as rather than omitting."""
    app = load_app(sample_graph_path)
    app._load_graph()
    coords = app._COORDS
    body = _route(app,
                  {"lat": float(coords[0][0]), "lon": float(coords[0][1])},
                  {"lat": float(coords[1][0]), "lon": float(coords[1][1])})
    types = {r["type"] for r in body["routes"]}
    assert types == {"fastest", "balanced", "coolest"}
    for r in body["routes"]:
        if "same_as" in r:
            assert r["same_as"] in types
