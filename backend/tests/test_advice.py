"""Bedrock advice: parsing, success path (mocked) and template fallback."""
import json

from conftest import get

ROUTES = [
    {"type": "fastest", "minutes": 15.0, "distance_km": 1.1, "shade_percent": 18.0,
     "exposure_index": 70.0, "geojson": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
    {"type": "balanced", "minutes": 17.0, "distance_km": 1.3, "shade_percent": 45.0,
     "exposure_index": 50.0, "geojson": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
    {"type": "coolest", "minutes": 20.0, "distance_km": 1.5, "shade_percent": 72.0,
     "exposure_index": 30.0, "geojson": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
]


def test_no_model_id_uses_template(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    advice, source = app.get_advice("elderly", 13, ROUTES)
    assert source == "template"
    assert set(advice) == {"en", "hi"}
    assert "coolest" in advice["en"].lower()
    assert advice["hi"]


def test_parse_accepts_plain_json():
    import app as a
    out = a._parse_advice_json('{"en":"keep left","hi":"बाएँ रहें"}')
    assert out == {"en": "keep left", "hi": "बाएँ रहें"}


def test_parse_strips_code_fences():
    import app as a
    text = '```json\n{"en":"a","hi":"b"}\n```'
    assert a._parse_advice_json(text) == {"en": "a", "hi": "b"}


def test_parse_rejects_garbage():
    import app as a
    assert a._parse_advice_json("not json at all") is None
    assert a._parse_advice_json('{"en":"only one"}') is None
    assert a._parse_advice_json("") is None


def test_bedrock_success_path(monkeypatch):
    """Mock boto3 bedrock-runtime and assert advice_source becomes 'bedrock'."""
    import app as a

    class FakeClient:
        def converse(self, **kwargs):
            payload = {"en": "Take the coolest route; it is 72% shaded.",
                       "hi": "सबसे ठंडा मार्ग लें।"}
            return {"output": {"message": {"content": [{"text": json.dumps(payload)}]}}}

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            assert service == "bedrock-runtime"
            return FakeClient()

    monkeypatch.setattr(a, "boto3", FakeBoto3)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model-id")
    advice, source = a.get_advice("adult", 13, ROUTES)
    assert source == "bedrock"
    assert advice["en"].startswith("Take the coolest")


def test_bedrock_failure_falls_back(monkeypatch):
    import app as a

    class BoomClient:
        def converse(self, **kwargs):
            raise RuntimeError("AccessDeniedException")

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            return BoomClient()

    monkeypatch.setattr(a, "boto3", FakeBoto3)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model-id")
    advice, source = a.get_advice("child", 12, ROUTES)
    assert source == "template"
    assert set(advice) == {"en", "hi"}


def test_route_endpoint_reports_advice_source(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    app._load_graph()
    c = {"lat": float(app._META["centre"][0]), "lon": float(app._META["centre"][1])}
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": c["lat"] - 0.001, "lon": c["lon"] - 0.001},
        "end": {"lat": c["lat"] + 0.002, "lon": c["lon"] + 0.002},
        "hour": 13, "walker": "vendor"})
    assert status == 200
    assert body["advice_source"] == "template"  # no model id configured
    assert body["advice"]["hi"]


def test_logging_is_failure_tolerant(load_app, sample_graph_path, monkeypatch):
    """A broken DynamoDB client must not break the response."""
    app = load_app(sample_graph_path, table_name="some-table")
    app._load_graph()

    class BoomClient:
        def put_item(self, **kwargs):
            raise RuntimeError("table missing")

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            return BoomClient()

    monkeypatch.setattr(app, "boto3", FakeBoto3)
    c = {"lat": float(app._META["centre"][0]), "lon": float(app._META["centre"][1])}
    status, body, _ = get(app, "/route", "POST", {
        "start": {"lat": c["lat"] - 0.001, "lon": c["lon"] - 0.001},
        "end": {"lat": c["lat"] + 0.002, "lon": c["lon"] + 0.002},
        "hour": 13, "walker": "adult"})
    assert status == 200
    assert "routes" in body
