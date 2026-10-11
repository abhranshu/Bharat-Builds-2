"""Nearest-node snapping correctness."""
import math

import numpy as np


def test_snaps_to_expected_node(load_app, test_graph_path):
    app = load_app(test_graph_path)
    app._load_graph()
    coords = app._COORDS
    # Just north of node A (id 0).
    i, d = app.snap_to_node(coords, 28.6311, 77.2190)
    assert i == 0  # node A
    assert d < 20.0


def test_snapping_matches_brute_force(load_app, sample_graph_path):
    app = load_app(sample_graph_path)
    app._load_graph()
    coords = app._COORDS
    lat, lon = 28.6335, 77.2215
    idx, dist = app.snap_to_node(coords, lat, lon)

    # Brute force with a haversine-ish check.
    best, best_d = None, 1e18
    for k in range(coords.shape[0]):
        a, b = coords[k]
        dlat = math.radians(a - lat)
        dlon = math.radians(b - lon) * math.cos(math.radians(lat))
        dd = 6371000.0 * math.hypot(dlat, dlon)
        if dd < best_d:
            best, best_d = k, dd
    assert idx == best
    assert abs(dist - best_d) < 1.0


def test_snap_distance_is_metres_not_degrees(load_app, test_graph_path):
    """Regression: degrees must be converted to radians before scaling."""
    app = load_app(test_graph_path)
    app._load_graph()
    _, d = app.snap_to_node(app._COORDS, 28.6310, 77.2190)
    assert d < 5.0  # exactly on node A
