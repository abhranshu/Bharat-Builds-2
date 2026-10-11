"""Validation and error-path tests."""
import json

from conftest import get


def _c(app):
    app._load_graph()
    return float(app._META["centre"][0]), float(app._META["centre"][1])


def test_outside_pilot_area(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    lat, lon = _c(app)
    status, body, headers = get(app, "/route", "POST", {
        "start": {"lat": lat + 5.0, "lon": lon + 5.0},
        "end": {"lat": lat, "lon": lon},
        "hour": 13, "walker": "adult"})
    assert status == 400
    assert "error" in body
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_bad_walker(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    lat, lon = _c(app)
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": lat, "lon": lon}, "end": {"lat": lat + 0.001, "lon": lon + 0.001},
        "hour": 13, "walker": "robot"})
    assert status == 400
    assert "walker" in body["error"]


def test_bad_hour(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    lat, lon = _c(app)
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": lat, "lon": lon}, "end": {"lat": lat + 0.001, "lon": lon + 0.001},
        "hour": 99, "walker": "adult"})
    assert status == 400
    assert "hour" in body["error"]


def test_non_numeric_coords(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": "north", "lon": 77.2}, "end": {"lat": 28.63, "lon": 77.22},
        "hour": 13, "walker": "adult"})
    assert status == 400
    assert "number" in body["error"]


def test_missing_body_objects(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, _ = get(app, "/route", "POST", {"hour": 13})
    assert status == 400


def test_invalid_json(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    event = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/route",
             "path": "/route", "body": "{not json"}
    res = app.handler(event)
    assert res["statusCode"] == 400
    assert "JSON" in json.loads(res["body"])["error"]


def test_method_not_allowed(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, _ = get(app, "/route", "GET")
    assert status == 405


def test_unknown_path(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, body, _ = get(app, "/nope")
    assert status == 404


def test_options_preflight(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    status, _, headers = get(app, "/route", "OPTIONS")
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "*"
