"""Determinismus-Checks für den Core außerhalb des Monolith-Einstiegspunkts."""

from Monolith import PhysicsCore, WorldConfig


def test_same_seed_produces_identical_state_hash_after_fixed_ticks() -> None:
    """Struktureller Check: gleicher Seed + gleiche Config muss bitgenau reproduzierbar bleiben."""
    cfg = WorldConfig(size=48)
    ticks = 200

    run_a = PhysicsCore(cfg, seed=1337)
    run_b = PhysicsCore(cfg, seed=1337)

    for _ in range(ticks):
        run_a.step()
        run_b.step()

    assert run_a.tick == run_b.tick == ticks
    assert run_a.state_hash() == run_b.state_hash()
