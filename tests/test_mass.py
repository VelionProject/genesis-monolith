"""Massen-/Energie-Sanity-Checks für stabile Simulationen."""

import numpy as np

from Monolith import PhysicsCore, WorldConfig


def test_total_field_mass_stays_non_negative_and_bounded() -> None:
    """Struktureller Integritätscheck: Gesamtmasse darf nicht negativ oder explodierend werden."""
    cfg = WorldConfig(size=64, clamp_max=10.0)
    core = PhysicsCore(cfg, seed=7)

    for _ in range(300):
        core.step()

    total_mass = float(
        np.sum(core.E + core.R1 + core.R2 + core.S + core.T + core.M)
    )

    # Untere Grenze: durch Clipping nie negativ.
    assert total_mass >= 0.0

    # Obere Grenze: alle Felder sind pro Zelle geklemmt.
    max_possible = cfg.size * cfg.size * (6 * cfg.clamp_max)
    assert total_mass <= max_possible
