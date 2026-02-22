"""Unit checks for activity overlay helper functions."""

import numpy as np

from Monolith import classify_activity_level, compute_activity_map


def test_compute_activity_map_returns_zero_when_no_change() -> None:
    prev = np.zeros((4, 4), dtype=np.float32)
    curr = np.zeros((4, 4), dtype=np.float32)
    activity = compute_activity_map(prev, curr)
    assert activity.shape == prev.shape
    assert float(activity.max()) == 0.0


def test_compute_activity_map_normalizes_by_observed_max_delta() -> None:
    prev = np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32)
    curr = np.array([[0.0, 2.0], [1.0, 0.0]], dtype=np.float32)
    activity = compute_activity_map(prev, curr)
    np.testing.assert_allclose(activity, np.array([[0.0, 1.0], [0.5, 0.0]], dtype=np.float32))


def test_classify_activity_level_uses_three_bands() -> None:
    assert classify_activity_level(0.01) == "stable"
    assert classify_activity_level(0.10) == "moderate"
    assert classify_activity_level(0.50) == "active"
