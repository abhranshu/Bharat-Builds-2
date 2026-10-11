"""Response contract shape for /health, /meta and /route."""
from conftest import get


def _centre(app):
    app._load_graph()
    return {"lat": float(app._META["centre"][0]), "lon": float(app._META["centre"][1])}


def test_health(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, headers = get(app, "/health")
    assert status == 200
    assert body == {"status": "ok", "graph_loaded": True}
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Content-Type"] == "application/json"


def test_meta(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, _ = get(app, "/meta")
    assert status == 200
    for key in ("pilot", "centre", "bbox", "available_hours", "walker_types",
                "disclaimer", "methods"):
        assert key in body
    assert body["available_hours"] == list(range(8, 19))
    assert set(body["walker_types"]) == {"elderly", "child", "vendor", "adult"}


def test_route_contract(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    app._load_graph()
    c = _centre(app)
    start = {"lat": c["lat"] - 0.001, "lon": c["lon"] - 0.001}
    end = {"lat": c["lat"] + 0.002, "lon": c["lon"] + 0.002}
    status, body, headers = get(app, "/route", "POST", {
        "start": start, "end": end, "hour": 13, "walker": "elderly", "lang": "en"})
    assert status == 200, body
    assert headers["Access-Control-Allow-Origin"] == "*"

    assert body["walker"] == "elderly"
    assert body["hour_used"] in list(range(8, 19))
    assert body["advice_source"] in ("bedrock", "template")
    assert isinstance(body["disclaimer"], str) and "estimate" in body["disclaimer"].lower()
    assert set(body["advice"]) == {"en", "hi"}
    assert isinstance(body["advice"]["en"], str) and body["advice"]["en"]

    assert len(body["routes"]) == 3
    assert [r["type"] for r in body["routes"]] == ["fastest", "balanced", "coolest"]
    for r in body["routes"]:
        for key in ("type", "minutes", "distance_km", "shade_percent",
                    "exposure_index", "geojson"):
            assert key in r, key
        assert isinstance(r["minutes"], (int, float))
        assert 0 <= r["shade_percent"] <= 100
        assert 0 <= r["exposure_index"] <= 100
        gj = r["geojson"]
        assert gj["type"] == "LineString"
        assert len(gj["coordinates"]) >= 2
        for lon, lat in gj["coordinates"]:
            assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert "graph_meta" in body and "pilot" in body["graph_meta"]


def test_hour_is_clamped_and_reported(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    app._load_graph()
    c = _centre(app)
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": c["lat"] - 0.001, "lon": c["lon"] - 0.001},
        "end": {"lat": c["lat"] + 0.002, "lon": c["lon"] + 0.002},
        "hour": 3, "walker": "adult"})
    assert status == 200
    assert body["hour_used"] == 8  # nearest available hour


def test_geometry_endpoints_match_request(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    app._load_graph()
    coords = app._COORDS
    c = _centre(app)
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": c["lat"] - 0.001, "lon": c["lon"] - 0.001},
        "end": {"lat": c["lat"] + 0.002, "lon": c["lon"] + 0.002},
        "hour": 13, "walker": "adult"})
    assert status == 200
    line = body["routes"][0]["geojson"]["coordinates"]
    # Endpoints should be near the snapped nodes, i.e. within the pilot area.
    assert abs(line[0][1] - c["lat"]) < 0.02
