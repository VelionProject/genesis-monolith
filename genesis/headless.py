from __future__ import annotations

from pathlib import Path

from .config import WorldConfig
from .core import PhysicsCore
from .persistence import EventLog


def run_headless(cfg: WorldConfig, seed: int, ticks: int, run_dir: Path) -> None:
    core = PhysicsCore(cfg, seed=seed)
    eventlog = EventLog(run_dir / "eventlog.jsonl")
    for _ in range(int(ticks)):
        core.step()
        every = int(cfg.snapshot_every_ticks)
        if every > 0 and (core.tick % every == 0):
            snap = core.snapshot()
            path = run_dir / "snapshots" / f"tick_{snap.tick:09d}.npz"
            snap.save_npz(path)
            eventlog.write({"t": core.tick, "type": "snapshot", "path": str(path)})
    eventlog.write({"t": core.tick, "type": "headless_done", "hash": core.state_hash()})
    eventlog.close()
