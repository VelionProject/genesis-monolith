"""Snapshot-Roundtrip-Tests für Persistenz und Replay-Fähigkeit."""

import numpy as np

from Monolith import PhysicsCore, Snapshot, WorldConfig


def test_snapshot_roundtrip_restores_full_core_state(tmp_path) -> None:
    """Struktureller Check: Snapshot speichern/laden muss den Kernzustand exakt wiederherstellen."""
    cfg = WorldConfig(size=40)
    core = PhysicsCore(cfg, seed=2026)

    for _ in range(120):
        core.step()

    original_hash = core.state_hash()
    snap_path = tmp_path / "state_snapshot.npz"
    core.snapshot().save_npz(snap_path)

    loaded_snap = Snapshot.load_npz(snap_path)
    restored = PhysicsCore(WorldConfig(size=40), seed=0)
    restored.load_snapshot(loaded_snap)

    assert restored.tick == core.tick
    assert restored.seed == core.seed
    assert restored.state_hash() == original_hash

    for name in ["E", "R1", "R2", "S", "T", "M", "energy_mask"]:
        assert np.array_equal(getattr(restored, name), getattr(core, name))
