"""Unit checks for derived visualization KPIs from the chemistry/physics mapping doc."""

import numpy as np

from Monolith import (
    FIELD_DISPLAY_NAMES,
    compute_conversion_efficiency,
    compute_strategy_bias,
    compute_viability_risk_map,
)


def test_field_display_names_match_normalization_contract() -> None:
    assert FIELD_DISPLAY_NAMES == {
        "E": "Energy",
        "R1": "Precursor",
        "R2": "Activated Intermediate",
        "S": "Structure",
        "T": "Byproduct",
        "M": "Genome Bias",
    }


def test_viability_risk_high_when_structure_high_and_energy_low() -> None:
    s = np.array([[1.0, 0.0]], dtype=np.float32)
    e = np.array([[0.0, 1.0]], dtype=np.float32)
    risk = compute_viability_risk_map(s, e)
    np.testing.assert_allclose(risk, np.array([[1.0, 0.0]], dtype=np.float32))


def test_conversion_efficiency_uses_global_stock_ratio() -> None:
    s = np.array([[2.0, 2.0]], dtype=np.float32)
    e = np.array([[1.0, 1.0]], dtype=np.float32)
    assert compute_conversion_efficiency(s, e) == 2.0


def test_strategy_bias_centered_around_half() -> None:
    assert compute_strategy_bias(np.array([0.5, 0.5], dtype=np.float32)) == 0.0
    assert compute_strategy_bias(np.array([1.0, 1.0], dtype=np.float32)) == 0.5
