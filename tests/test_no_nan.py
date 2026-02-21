"""Numerik-Stabilität: keine NaN/Inf-Werte in längeren Läufen."""

import numpy as np

from Monolith import PhysicsCore, WorldConfig


def test_no_nan_or_inf_in_long_run() -> None:
    """Struktureller Integritätscheck: numerische Werte müssen endlich bleiben."""
    cfg = WorldConfig(size=72, enable_M=True, M_init_mode="noise")
    core = PhysicsCore(cfg, seed=99)

    for _ in range(500):
        core.step()

    for field in (core.E, core.R1, core.R2, core.S, core.T, core.M):
        assert np.isfinite(field).all()
