from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .config import WorldConfig
from .core import PhysicsCore
from .observability import entropy_1d
from .persistence import Snapshot


# =========================
# Layer: Hunter (Agent)
# =========================

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def now_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def quick_reason(core: PhysicsCore, cfg: WorldConfig) -> str:
    # simple, actionable diagnosis (good enough for v1)
    E = core.E
    S = core.S
    T = core.T
    alive = float(S.max())
    e_mean = float(E.mean())
    e_min = float(E.min())
    t_mean = float(T.mean()) if cfg.enable_T else 0.0
    if alive < cfg.proto_S_threshold:
        if e_mean < 0.02 or e_min < 1e-4:
            return "E_STARVATION"
        if cfg.enable_T and t_mean > 0.2:
            return "TOXIN_OVERLOAD"
        return "NO_STABLE_STRUCTURE"
    # still has structure but not thriving
    if e_mean < 0.02:
        return "LOW_E_SUPPORT"
    if cfg.enable_T and t_mean > 0.2:
        return "TOXIN_PRESSURE"
    return "WEAK_STABILITY"

def alive_mask(core: PhysicsCore, thr: float) -> np.ndarray:
    return (core.S > thr)

def alive_score(core: PhysicsCore, thr: float) -> Tuple[int, float]:
    mask = alive_mask(core, thr)
    return int(mask.sum()), float(core.S.max())

def save_anomaly_bundle(
    base_dir: Path,
    cfg: WorldConfig,
    seed: int,
    birth_snap: Optional[Snapshot],
    last_snap: Snapshot,
    survival_ticks: int,
    birth_tick: Optional[int],
    reason: str,
) -> Path:
    run_id = f"{now_id()}_seed{seed}"
    out = ensure_dir(base_dir / run_id)
    snaps_dir = ensure_dir(out / "snapshots")

    # snapshots
    last_path = snaps_dir / f"tick_{last_snap.tick:09d}.npz"
    last_snap.save_npz(last_path)

    birth_path = None
    if birth_snap is not None:
        birth_path = snaps_dir / f"birth_{birth_snap.tick:09d}.npz"
        birth_snap.save_npz(birth_path)

    summary = {
        "id": run_id,
        "seed": seed,
        "cfg": asdict(cfg),
        "birth_tick": birth_tick,
        "end_tick": last_snap.tick,
        "survival_ticks": survival_ticks,
        "reason": reason,
        "paths": {
            "birth": str(birth_path) if birth_path else None,
            "end": str(last_path),
        },
        "kpi": {
            "S_max": float(last_snap.arrays["S"].max()),
            "S_area_proto": int((last_snap.arrays["S"] > cfg.proto_S_threshold).sum()),
            "S_area_alive": int((last_snap.arrays["S"] > cfg.alive_S_threshold).sum()),
            "E_mean": float(last_snap.arrays["E"].mean()),
            "T_mean": float(last_snap.arrays["T"].mean()) if cfg.enable_T else 0.0,
            "M_entropy": entropy_1d(last_snap.arrays["M"]) if cfg.enable_M else 0.0,
        }
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return out

# UI-side thread agent
def _seed_stream(rng: np.random.Generator, count: int) -> List[int]:
    return [int(rng.integers(0, 2**31 - 1)) for _ in range(count)]
