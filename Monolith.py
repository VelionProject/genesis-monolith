# Monolith.py
# Genesis v1.4+ — Monolith + Hunter Agent + Anomaly Inbox (UI-first, no tkinter)
#
# Requirements:
#   pip install numpy PySide6 pyqtgraph matplotlib
#
# Run UI:
#   python Monolith.py
#
# Headless:
#   python Monolith.py --headless --ticks 200000 --snapshot-every 5000
#
# Note:
#   CLI currently supports seed/size/headless/ticks/snapshot-every.
#   Replay/sweep commands are planned for a later milestone.
#
# NEW (UI):
#   - Hunter Agent ON/OFF: brute-force seeds in background
#   - Anomaly Inbox: shows found seeds; double-click loads snapshot into cockpit (fork)

from __future__ import annotations

import sys
import json
import time
import argparse
import hashlib
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# =========================
# Layer: Config
# =========================

@dataclass
class WorldConfig:
    # space
    size: int = 256
    neighborhood: str = "moore"  # used by cluster labeling in observability

    # diffusion
    diff_E: float = 0.35
    diff_R: float = 0.08
    diff_T: float = 0.18
    diff_S: float = 0.0
    diff_M: float = 0.0  # keep M mostly bound; default 0

    # decay
    decay_E: float = 0.02
    decay_R: float = 0.005
    decay_S: float = 0.01
    decay_T: float = 0.06
    decay_M: float = 0.000  # optional drift back to baseline

    # maintenance
    S_energy_per_tick: float = 0.01  # base; can be scaled by M

    # reactions
    k1: float = 0.03   # R1 + E -> R2
    k2: float = 0.02   # R2 + E -> S
    k3: float = 0.004  # S -> R1
    k4: float = 0.006  # R2 + E -> T (optional)
    enable_T: bool = True

    # stability
    clamp_max: float = 10.0

    # energy source
    energy_mode: str = "hotspots"  # "hotspots" | "gradient"
    energy_strength: float = 0.03
    hotspot_count: int = 4
    hotspot_radius_min: int = 12
    hotspot_radius_max: int = 20

    # diffusion damping by structure
    diffusion_damping_strength: float = 0.6

    # Phase 1: M (proto-genome)
    enable_M: bool = False
    M_init_mode: str = "baseline"  # "baseline" | "noise" | "islands"
    M_baseline: float = 0.5
    M_island_count: int = 12
    M_island_radius_min: int = 4
    M_island_radius_max: int = 10

    # M copy + mutation
    M_copy_on_growth: bool = True
    M_mut_sigma: float = 0.02
    M_mut_stress_factor: float = 4.0  # mutation rises when E is low
    M_parent_from_neighbors: bool = True  # parent picked by max S in Moore neighborhood

    # M -> parameter coupling (tradeoffs)
    k2_scale_min: float = 0.4
    k2_scale_max: float = 1.6
    maint_scale_min: float = 0.6
    maint_scale_max: float = 1.8

    # Observability thresholds
    proto_S_threshold: float = 0.2
    alive_S_threshold: float = 0.5

    # Replicator detection
    detect_enabled: bool = True
    detect_every_n_frames: int = 6
    detect_match_dt_ticks: int = 300
    detect_similarity_threshold: float = 0.08  # smaller = stricter

    # persistence defaults
    snapshot_every_ticks: int = 2000
    out_dir: str = "runs"

    # Hunter defaults (UI uses these as initial values)
    hunter_enabled: bool = False
    hunter_target_survival_ticks: int = 10_000
    hunter_max_ticks_per_seed: int = 60_000
    hunter_seeds_per_batch: int = 12
    hunter_anomalies_dir: str = "runs/anomalies"


# =========================
# Layer: Persistence
# =========================

@dataclass
class Snapshot:
    tick: int
    seed: int
    cfg: Dict
    rng_state: Dict
    meta: Dict
    arrays: Dict[str, np.ndarray]

    def save_npz(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "tick": self.tick,
            "seed": self.seed,
            "cfg": self.cfg,
            "rng_state": self.rng_state,
            "meta": self.meta,
            "arrays": list(self.arrays.keys()),
        }
        np.savez_compressed(path, __meta__=json.dumps(meta), **self.arrays)

    @staticmethod
    def load_npz(path: Path) -> "Snapshot":
        data = np.load(path, allow_pickle=False)
        meta = json.loads(str(data["__meta__"]))
        arrays = {k: data[k] for k in meta["arrays"]}
        return Snapshot(
            tick=int(meta["tick"]),
            seed=int(meta["seed"]),
            cfg=meta["cfg"],
            rng_state=meta["rng_state"],
            meta=meta.get("meta", {}),
            arrays=arrays
        )


class EventLog:
    """Append-only JSONL. Observability only (does not drive the core)."""
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, event: Dict) -> None:
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# =========================
# Layer: Core (forward-only, deterministic)
# =========================

class PhysicsCore:
    """
    Fields are float32 2D arrays:
      E, R1, R2, S, T, M
    Deterministic given:
      seed + cfg + initial arrays + rng_state + energy_mask
    """

    def __init__(self, cfg: WorldConfig, seed: int = 12345):
        self.cfg = cfg
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.tick = 0

        n = cfg.size
        self.E  = np.zeros((n, n), dtype=np.float32)
        self.R1 = np.full((n, n), 0.5, dtype=np.float32)
        self.R2 = np.zeros((n, n), dtype=np.float32)
        self.S  = np.zeros((n, n), dtype=np.float32)
        self.T  = np.zeros((n, n), dtype=np.float32)
        self.M  = np.full((n, n), cfg.M_baseline, dtype=np.float32)

        self.energy_mask = np.zeros((n, n), dtype=np.float32)  # stored in snapshot
        self._make_energy_sources()
        self._init_M()

    def reset(self, same_seed: bool = True) -> None:
        if not same_seed:
            self.seed = int(self.rng.integers(0, 2**31 - 1))
        self.rng = np.random.default_rng(self.seed)

        self.tick = 0
        self.E.fill(0.0)
        self.R1.fill(0.5)
        self.R2.fill(0.0)
        self.S.fill(0.0)
        self.T.fill(0.0)
        self.M.fill(self.cfg.M_baseline)

        self._make_energy_sources()
        self._init_M()

    def snapshot(self) -> Snapshot:
        rng_state = self.rng.bit_generator.state
        arrays = {
            "E": self.E.copy(),
            "R1": self.R1.copy(),
            "R2": self.R2.copy(),
            "S": self.S.copy(),
            "T": self.T.copy(),
            "M": self.M.copy(),
            "energy_mask": self.energy_mask.copy(),
        }
        meta = {"det_hash": self.state_hash()}
        return Snapshot(
            tick=self.tick,
            seed=self.seed,
            cfg=asdict(self.cfg),
            rng_state=rng_state,
            meta=meta,
            arrays=arrays
        )

    def load_snapshot(self, snap: Snapshot) -> None:
        self.tick = int(snap.tick)
        self.seed = int(snap.seed)
        self.cfg = WorldConfig(**snap.cfg)

        self.rng = np.random.default_rng()
        self.rng.bit_generator.state = snap.rng_state

        n = self.cfg.size
        if self.E.shape != (n, n):
            self.E  = np.zeros((n, n), dtype=np.float32)
            self.R1 = np.zeros((n, n), dtype=np.float32)
            self.R2 = np.zeros((n, n), dtype=np.float32)
            self.S  = np.zeros((n, n), dtype=np.float32)
            self.T  = np.zeros((n, n), dtype=np.float32)
            self.M  = np.zeros((n, n), dtype=np.float32)
            self.energy_mask = np.zeros((n, n), dtype=np.float32)

        for name in ["E", "R1", "R2", "S", "T", "M", "energy_mask"]:
            arr = snap.arrays[name].astype(np.float32, copy=False)
            getattr(self, name)[:] = arr

    def state_hash(self) -> str:
        """Determinism checksum over core state arrays + tick + seed."""
        h = hashlib.blake2b(digest_size=16)
        h.update(int(self.tick).to_bytes(8, "little", signed=False))
        h.update(int(self.seed).to_bytes(8, "little", signed=False))
        for a in (self.E, self.R1, self.R2, self.S, self.T, self.M, self.energy_mask):
            h.update(np.ascontiguousarray(a).view(np.uint8))
        return h.hexdigest()

    def _init_M(self) -> None:
        c = self.cfg
        if not c.enable_M:
            self.M.fill(0.0)
            return

        if c.M_init_mode == "baseline":
            self.M.fill(c.M_baseline)
        elif c.M_init_mode == "noise":
            self.M[:] = np.clip(
                c.M_baseline + self.rng.normal(0, 0.06, self.M.shape).astype(np.float32),
                0.0, 1.0
            )
        elif c.M_init_mode == "islands":
            self.M.fill(c.M_baseline)
            n = c.size
            for _ in range(c.M_island_count):
                cx = int(self.rng.integers(0, n))
                cy = int(self.rng.integers(0, n))
                r  = int(self.rng.integers(c.M_island_radius_min, c.M_island_radius_max + 1))
                val = float(np.clip(c.M_baseline + self.rng.normal(0, 0.25), 0.0, 1.0))
                ys = np.arange(n)
                xs = np.arange(n)
                dy = np.minimum(np.abs(ys - cy), n - np.abs(ys - cy))[:, None]
                dx = np.minimum(np.abs(xs - cx), n - np.abs(xs - cx))[None, :]
                circle = ((dx * dx + dy * dy) <= (r * r))
                self.M[circle] = val
        else:
            self.M.fill(c.M_baseline)

    def _make_energy_sources(self) -> None:
        c = self.cfg
        n = c.size
        mask = np.zeros((n, n), dtype=np.float32)

        if c.energy_mode == "hotspots":
            for _ in range(c.hotspot_count):
                cx = int(self.rng.integers(0, n))
                cy = int(self.rng.integers(0, n))
                r  = int(self.rng.integers(c.hotspot_radius_min, c.hotspot_radius_max + 1))
                ys = np.arange(n)
                xs = np.arange(n)
                dy = np.minimum(np.abs(ys - cy), n - np.abs(ys - cy))[:, None]
                dx = np.minimum(np.abs(xs - cx), n - np.abs(xs - cx))[None, :]
                circle = ((dx * dx + dy * dy) <= (r * r)).astype(np.float32)
                mask += circle
            mask = np.clip(mask, 0.0, 1.0)
        elif c.energy_mode == "gradient":
            y = np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
            mask = np.repeat(y, n, axis=1)
        else:
            mask.fill(0.0)

        self.energy_mask = mask

    @staticmethod
    def _laplace(a: np.ndarray) -> np.ndarray:
        return (
            np.roll(a,  1, 0) + np.roll(a, -1, 0) +
            np.roll(a,  1, 1) + np.roll(a, -1, 1) -
            4.0 * a
        )

    def _moore_parent_M_by_maxS(self) -> np.ndarray:
        """Vectorized parent selection: neighbor with max S in Moore (8) + self."""
        S = self.S
        M = self.M
        shifts = [
            (0, 0),
            (-1, 0), (1, 0), (0, -1), (0, 1),
            (-1, -1), (-1, 1), (1, -1), (1, 1),
        ]
        Sc, Mc = [], []
        for dy, dx in shifts:
            Sc.append(np.roll(np.roll(S, dy, axis=0), dx, axis=1))
            Mc.append(np.roll(np.roll(M, dy, axis=0), dx, axis=1))
        Sc_stack = np.stack(Sc, axis=0)
        Mc_stack = np.stack(Mc, axis=0)
        idx = np.argmax(Sc_stack, axis=0)
        parent_M = np.take_along_axis(Mc_stack, idx[None, :, :], axis=0)[0]
        return parent_M

    def step(self) -> None:
        """One forward-only tick."""
        c = self.cfg
        self.tick += 1

        # (1) diffusion with structure damping
        damping = 1.0 - np.clip(self.S, 0.0, 1.0) * c.diffusion_damping_strength
        self.E  += c.diff_E * self._laplace(self.E) * damping
        self.R1 += c.diff_R * self._laplace(self.R1) * damping
        self.R2 += c.diff_R * self._laplace(self.R2) * damping
        if c.enable_T:
            self.T += c.diff_T * self._laplace(self.T) * damping
        if c.enable_M and c.diff_M > 0.0:
            self.M += c.diff_M * self._laplace(self.M) * np.clip(self.S, 0.0, 1.0)

        # (2) energy inflow
        self.E += (c.energy_strength * self.energy_mask).astype(np.float32)

        # (3) reactions (delta-based)
        E, R1, R2, S, T, M = self.E, self.R1, self.R2, self.S, self.T, self.M

        if c.enable_M:
            k2_eff = c.k2 * (c.k2_scale_min + (c.k2_scale_max - c.k2_scale_min) * np.clip(M, 0.0, 1.0))
        else:
            k2_eff = c.k2

        lim_1 = np.minimum(E, R1)
        d1 = lim_1 * c.k1

        E_after_d1 = E - d1
        R2_after_d1 = R2 + d1
        lim_2 = np.minimum(E_after_d1, R2_after_d1)
        d2 = lim_2 * k2_eff

        d3 = S * c.k3

        if c.enable_T and c.k4 > 0.0:
            E_after_d1d2 = E - d1 - d2
            R2_after_d1d2 = np.maximum(R2 + d1 - d2, 0.0)
            lim_4 = np.minimum(E_after_d1d2, R2_after_d1d2)
            d4 = lim_4 * c.k4
        else:
            d4 = 0.0

        E  -= d1
        R1 -= d1
        R2 += d1

        E  -= d2
        R2 -= d2
        S  += d2

        S  -= d3
        R1 += d3

        if c.enable_T and c.k4 > 0.0:
            E  -= d4
            R2 -= d4
            T  += d4

        # (3b) Phase 1: M copy-on-growth + mutation
        if c.enable_M and c.M_copy_on_growth:
            grow = (d2 > 0.0)
            if np.any(grow):
                parent_M = self._moore_parent_M_by_maxS() if c.M_parent_from_neighbors else M
                stress = np.clip((0.1 - E) / 0.1, 0.0, 1.0).astype(np.float32)
                sigma = c.M_mut_sigma * (1.0 + c.M_mut_stress_factor * stress)
                noise = self.rng.normal(0.0, 1.0, size=M.shape).astype(np.float32) * sigma
                M_new = np.clip(parent_M + noise, 0.0, 1.0)
                M[grow] = M_new[grow]
            M[self.S < 1e-4] = c.M_baseline

        # (4) maintenance + decay
        if c.enable_M:
            maint_eff = c.S_energy_per_tick * (
                c.maint_scale_min + (c.maint_scale_max - c.maint_scale_min) * np.clip(M, 0.0, 1.0)
            )
            E -= (S * maint_eff)
        else:
            E -= (S * c.S_energy_per_tick)

        E  *= (1.0 - c.decay_E)
        R1 *= (1.0 - c.decay_R)
        R2 *= (1.0 - c.decay_R)
        S  *= (1.0 - c.decay_S)
        if c.enable_T:
            T *= (1.0 - c.decay_T)
        if c.enable_M and c.decay_M > 0.0:
            M[:] = np.clip(M * (1.0 - c.decay_M) + c.M_baseline * c.decay_M, 0.0, 1.0)

        energy_starved = (E < 0.001).astype(np.float32)
        bleed = np.minimum(S, 0.002 * energy_starved)
        S  -= bleed
        R1 += bleed

        # (5) clamp + NaN guard
        np.clip(self.E,  0.0, c.clamp_max, out=self.E)
        np.clip(self.R1, 0.0, c.clamp_max, out=self.R1)
        np.clip(self.R2, 0.0, c.clamp_max, out=self.R2)
        np.clip(self.S,  0.0, c.clamp_max, out=self.S)
        if c.enable_T:
            np.clip(self.T, 0.0, c.clamp_max, out=self.T)
        if c.enable_M:
            np.clip(self.M, 0.0, 1.0, out=self.M)

        if not (np.isfinite(self.E).all() and np.isfinite(self.R1).all() and np.isfinite(self.S).all() and np.isfinite(self.M).all()):
            self.reset(same_seed=True)


# =========================
# Layer: Observability
# =========================

@dataclass
class ClusterFP:
    cid: int
    tick: int
    size: int
    cy: float
    cx: float
    meanS: float
    maxS: float
    meanM: float
    varM: float
    meanE: float

    def vec(self) -> np.ndarray:
        return np.array([
            self.size / 1000.0,
            self.meanS,
            self.maxS,
            self.meanM,
            self.varM * 10.0,
            self.meanE / 2.0,
        ], dtype=np.float32)


def label_clusters_bool(mask: np.ndarray, moore: bool = True, torus: bool = True) -> Tuple[np.ndarray, int]:
    """Low-cadence BFS connected-component labeling for boolean mask."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    cid = 0
    neigh = [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)] if moore else [(-1,0),(1,0),(0,-1),(0,1)]
    from collections import deque
    q = deque()
    ys, xs = np.where(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if labels[y0, x0] != 0:
            continue
        cid += 1
        labels[y0, x0] = cid
        q.append((y0, x0))
        while q:
            y, x = q.popleft()
            for dy, dx in neigh:
                ny = y + dy
                nx = x + dx
                if torus:
                    ny %= h
                    nx %= w
                else:
                    if ny < 0 or ny >= h or nx < 0 or nx >= w:
                        continue
                if mask[ny, nx] and labels[ny, nx] == 0:
                    labels[ny, nx] = cid
                    q.append((ny, nx))
    return labels, cid


def compute_fingerprints(core: PhysicsCore, labels: np.ndarray, n_clusters: int) -> List[ClusterFP]:
    S, M, E = core.S, core.M, core.E
    fps: List[ClusterFP] = []
    for cid in range(1, n_clusters + 1):
        pts = np.where(labels == cid)
        if pts[0].size == 0:
            continue
        ys = pts[0].astype(np.float32)
        xs = pts[1].astype(np.float32)
        Sc = S[pts]
        Mc = M[pts]
        Ec = E[pts]
        fps.append(ClusterFP(
            cid=cid,
            tick=core.tick,
            size=int(pts[0].size),
            cy=float(ys.mean()),
            cx=float(xs.mean()),
            meanS=float(Sc.mean()),
            maxS=float(Sc.max()),
            meanM=float(Mc.mean()),
            varM=float(Mc.var()),
            meanE=float(Ec.mean()),
        ))
    return fps


def match_replications(
    fps_now: List[ClusterFP],
    history: List[ClusterFP],
    dt_ticks: int,
    sim_thresh: float
) -> List[Tuple[ClusterFP, ClusterFP, float]]:
    out: List[Tuple[ClusterFP, ClusterFP, float]] = []
    if not history or not fps_now:
        return out
    tick_now = fps_now[0].tick
    recent = [h for h in history if (tick_now - h.tick) <= dt_ticks]
    if not recent:
        return out
    H = np.stack([h.vec() for h in recent], axis=0)
    for child in fps_now:
        v = child.vec()[None, :]
        d = np.linalg.norm(H - v, axis=1)
        j = int(np.argmin(d))
        dist = float(d[j])
        if dist < sim_thresh:
            out.append((child, recent[j], dist))
    return out


def entropy_1d(x: np.ndarray, bins: int = 24, eps: float = 1e-12) -> float:
    """Shannon entropy of x in [0,1] (approximately)."""
    if x.size == 0:
        return 0.0
    h, _ = np.histogram(x, bins=bins, range=(0.0, 1.0), density=False)
    p = h.astype(np.float64)
    p = p / max(p.sum(), 1.0)
    p = p[p > 0]
    return float(-(p * np.log(p + eps)).sum())


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

# lazy import: PySide6 only when UI
# (headless should not require Qt to run)
# We'll define thread class inside run_ui.


# =========================
# Layer: UI (optional)
# =========================

def run_ui(cfg: WorldConfig, seed: int, run_dir: Path) -> None:
    import importlib
    import importlib.util

    from PySide6.QtCore import Qt, QTimer, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget,
        QHBoxLayout, QVBoxLayout, QPushButton,
        QSlider, QLabel, QCheckBox, QGroupBox, QSpinBox,
        QListWidget, QListWidgetItem, QSplitter, QLineEdit,
        QScrollArea
    )

    # Renderer selection (UI-only): prefer PyQtGraph for high-frequency image updates,
    # keep Matplotlib as a compatibility fallback if PyQtGraph is not installed.
    pg = None
    Figure = None
    FigureCanvas = None
    plot_backend = "none"
    if importlib.util.find_spec("pyqtgraph") is not None:
        pg = importlib.import_module("pyqtgraph")
        plot_backend = "pyqtgraph"
    elif importlib.util.find_spec("matplotlib") is not None:
        Figure = importlib.import_module("matplotlib.figure").Figure
        FigureCanvas = importlib.import_module("matplotlib.backends.backend_qtagg").FigureCanvasQTAgg
        plot_backend = "matplotlib"
    else:
        raise RuntimeError("UI plotting backend not available. Install pyqtgraph or matplotlib.")

    class SeedHunter(QThread):
        status = Signal(str)
        found = Signal(str)  # path to summary.json
        stats = Signal(int, int)  # tested, found

        def __init__(self, base_cfg: WorldConfig):
            super().__init__()
            self.base_cfg = base_cfg
            self._running = False
            self._tested = 0
            self._found = 0
            self._rng = np.random.default_rng(int(time.time()) & 0x7FFFFFFF)
            self._tested_seeds_db_path: Optional[Path] = None
            self._tested_seed_conn: Optional[sqlite3.Connection] = None

            # dynamic settings (UI can update)
            self.target_survival = base_cfg.hunter_target_survival_ticks
            self.max_ticks = base_cfg.hunter_max_ticks_per_seed
            self.batch = base_cfg.hunter_seeds_per_batch

        # Structural change: use a compact SQLite cache (single file) instead of ever-growing text logs.
        def _init_seed_cache(self, anomalies_dir: Path) -> None:
            self._tested_seeds_db_path = anomalies_dir / "tested_seeds.sqlite3"
            conn = sqlite3.connect(self._tested_seeds_db_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("CREATE TABLE IF NOT EXISTS tested_seeds (seed INTEGER PRIMARY KEY)")

            # Structural change: migrate legacy text log once into SQLite and remove it to save disk space.
            legacy_log = anomalies_dir / "tested_seeds.log"
            if legacy_log.exists():
                for line in legacy_log.read_text(encoding="utf-8").splitlines():
                    val = line.strip()
                    if not val:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO tested_seeds(seed) VALUES (?)",
                        (int(val),),
                    )
                conn.commit()
                legacy_log.unlink(missing_ok=True)

            self._tested_seed_conn = conn

        # Structural change: cache update is in-place (INSERT OR IGNORE) and persists across restarts.
        def _mark_seed_tested(self, seed: int) -> None:
            if self._tested_seed_conn is None:
                return
            self._tested_seed_conn.execute(
                "INSERT OR IGNORE INTO tested_seeds(seed) VALUES (?)",
                (int(seed),),
            )
            self._tested_seed_conn.commit()

        # Structural change: membership check goes directly against persistent cache to avoid re-testing seeds.
        def _seed_was_tested(self, seed: int) -> bool:
            if self._tested_seed_conn is None:
                return False
            row = self._tested_seed_conn.execute(
                "SELECT 1 FROM tested_seeds WHERE seed=? LIMIT 1",
                (int(seed),),
            ).fetchone()
            return row is not None

        # Structural change: generate unique candidate seeds while skipping those already in the persistent cache.
        def _seed_batch_unique(self) -> List[int]:
            unique: List[int] = []
            target = max(1, int(self.batch))
            while len(unique) < target:
                candidate = int(self._rng.integers(0, 2**31 - 1))
                if candidate in unique:
                    continue
                if self._seed_was_tested(candidate):
                    continue
                unique.append(candidate)
            return unique

        # Structural change: explicit cache close keeps sqlite file consistent when hunter stops.
        def _close_seed_cache(self) -> None:
            if self._tested_seed_conn is None:
                return
            self._tested_seed_conn.close()
            self._tested_seed_conn = None

        def stop(self):
            self._running = False

        def run(self):
            self._running = True
            anomalies_dir = Path(self.base_cfg.hunter_anomalies_dir)
            ensure_dir(anomalies_dir)
            self._init_seed_cache(anomalies_dir)
            known_tested = 0
            if self._tested_seed_conn is not None:
                known_tested = int(self._tested_seed_conn.execute("SELECT COUNT(*) FROM tested_seeds").fetchone()[0])

            self.status.emit(f"Hunter: running (known_tested={known_tested})")
            try:
                while self._running:
                    seeds = self._seed_batch_unique()
                    for sd in seeds:
                        if not self._running:
                            break

                        # fresh core per seed (deterministic run)
                        c = WorldConfig(**asdict(self.base_cfg))
                        core = PhysicsCore(c, seed=sd)

                        birth_tick = None
                        birth_snap = None
                        alive_started = False
                        alive_start_tick = None

                        # run loop
                        for _ in range(int(self.max_ticks)):
                            if not self._running:
                                break
                            core.step()

                            # detect "alive" by alive threshold area
                            area_alive, smax = alive_score(core, c.alive_S_threshold)
                            if (not alive_started) and (area_alive > 0):
                                alive_started = True
                                alive_start_tick = core.tick
                                birth_tick = core.tick
                                birth_snap = core.snapshot()

                            # success: survived target ticks since first alive
                            if alive_started and alive_start_tick is not None:
                                if (core.tick - alive_start_tick) >= int(self.target_survival):
                                    last_snap = core.snapshot()
                                    reason = "SURVIVED_TARGET"
                                    out = save_anomaly_bundle(
                                        base_dir=anomalies_dir,
                                        cfg=c,
                                        seed=sd,
                                        birth_snap=birth_snap,
                                        last_snap=last_snap,
                                        survival_ticks=int(core.tick - alive_start_tick),
                                        birth_tick=birth_tick,
                                        reason=reason
                                    )
                                    self._found += 1
                                    self._tested += 1
                                    self._mark_seed_tested(sd)
                                    self.stats.emit(self._tested, self._found)
                                    self.found.emit(str(out / "summary.json"))
                                    self.status.emit(f"Hunter: FOUND seed={sd} survived={core.tick - alive_start_tick}")
                                    break

                            # failure after having been alive: died out (no proto area)
                            if alive_started:
                                area_proto = int((core.S > c.proto_S_threshold).sum())
                                if area_proto == 0:
                                    last_snap = core.snapshot()
                                    reason = quick_reason(core, c)
                                    out = save_anomaly_bundle(
                                        base_dir=anomalies_dir,
                                        cfg=c,
                                        seed=sd,
                                        birth_snap=birth_snap,
                                        last_snap=last_snap,
                                        survival_ticks=int(core.tick - (alive_start_tick or core.tick)),
                                        birth_tick=birth_tick,
                                        reason=reason
                                    )
                                    self._found += 1
                                    self._tested += 1
                                    self._mark_seed_tested(sd)
                                    self.stats.emit(self._tested, self._found)
                                    self.found.emit(str(out / "summary.json"))
                                    self.status.emit(f"Hunter: anomaly seed={sd} reason={reason}")
                                    break

                        # if no anomaly found, just count as tested
                        if self._running:
                            self._tested += 1
                            self._mark_seed_tested(sd)
                            self.stats.emit(self._tested, self._found)

                    # tiny breather so UI stays snappy
                    self.msleep(15)
            finally:
                self._close_seed_cache()
                self.status.emit("Hunter: stopped")

    class GenesisCockpit(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Genesis v1.4+ — Monolith + Hunter + Inbox")

            self.cfg = cfg
            self.core = PhysicsCore(self.cfg, seed=seed)

            self.run_dir = run_dir
            self.eventlog = EventLog(self.run_dir / "eventlog.jsonl")
            self.plot_backend = plot_backend

            self.sim_running = False
            self.timer = QTimer(self)
            self.timer.setInterval(16)
            self.timer.timeout.connect(self.on_timer)

            # detection state
            self._frame_counter = 0
            self._fp_history: List[ClusterFP] = []
            self._replication_events = 0

            # hunter
            self.hunter = SeedHunter(self.cfg)
            self.hunter.status.connect(self.on_hunter_status)
            self.hunter.found.connect(self.on_hunter_found)
            self.hunter.stats.connect(self.on_hunter_stats)

            # UI root
            central = QWidget()
            self.setCentralWidget(central)

            # Structural UI change: vertical root layout allows a persistent bottom toggle button
            # while keeping main content inside a resize-aware splitter.
            root = QVBoxLayout(central)

            self.splitter = QSplitter(Qt.Horizontal)
            self.splitter.setChildrenCollapsible(False)
            root.addWidget(self.splitter, 1)

            # Structural UI change: dedicated sidebar panel + scroll area prevents clipping when
            # controls grow and keeps future sidebar extensions safe.
            self.sidebar = QWidget()
            self.sidebar_layout = QVBoxLayout(self.sidebar)
            self.sidebar_layout.setContentsMargins(8, 8, 8, 8)

            self.sidebar_scroll = QScrollArea()
            self.sidebar_scroll.setWidgetResizable(True)
            self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.sidebar_scroll.setWidget(self.sidebar)

            self.sidebar_panel = QWidget()
            self.sidebar_panel_layout = QVBoxLayout(self.sidebar_panel)
            self.sidebar_panel_layout.setContentsMargins(0, 0, 0, 0)
            self.sidebar_panel_layout.addWidget(self.sidebar_scroll)
            self.sidebar_panel.setMinimumWidth(360)

            self.plot_panel = self._build_plots()
            self.plot_panel.setMinimumWidth(640)

            self.splitter.addWidget(self.sidebar_panel)
            self.splitter.addWidget(self.plot_panel)
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([420, 1100])

            self._build_sidebar()

            # Structural UI change: persistent footer control keeps sidebar show/hide reachable
            # even when the sidebar is currently hidden.
            footer_bar = QHBoxLayout()
            self.btn_sidebar_toggle = QPushButton("Sidebar ausblenden")
            self.btn_sidebar_toggle.clicked.connect(self.toggle_sidebar)
            footer_bar.addWidget(self.btn_sidebar_toggle)
            footer_bar.addStretch(1)
            root.addLayout(footer_bar)

            # inbox auto-refresh
            self.inbox_timer = QTimer(self)
            self.inbox_timer.setInterval(2000)
            self.inbox_timer.timeout.connect(self.scan_inbox)
            self.inbox_timer.start()

            self.refresh_plots()
            self.scan_inbox()

        def closeEvent(self, event):
            try:
                if self.hunter.isRunning():
                    self.hunter.stop()
                    self.hunter.wait(500)
                self.eventlog.close()
            finally:
                super().closeEvent(event)


        # Structural UI change: toggle sidebar visibility with safe splitter resizing.
        def toggle_sidebar(self):
            if self.sidebar_panel.isVisible():
                self.sidebar_panel.hide()
                self.btn_sidebar_toggle.setText("Sidebar einblenden")
            else:
                self.sidebar_panel.show()
                self.btn_sidebar_toggle.setText("Sidebar ausblenden")
                # Restore readable default ratio after re-showing the sidebar.
                self.splitter.setSizes([420, 1100])

        # -------------------------
        # Sidebar
        # -------------------------
        def _build_sidebar(self):
            box = self.sidebar_layout
            box.addWidget(QLabel("<b>CONTROLS</b>"))

            self.btn_start = QPushButton("START")
            self.btn_start.clicked.connect(self.toggle_start_pause)
            box.addWidget(self.btn_start)

            step_group = QGroupBox("Step")
            step_layout = QHBoxLayout(step_group)
            for label, n in [("×1", 1), ("×10", 10), ("×100", 100)]:
                b = QPushButton(label)
                b.clicked.connect(lambda _, nn=n: self.step_n(nn))
                step_layout.addWidget(b)
            box.addWidget(step_group)

            box.addWidget(QLabel("Speed (Ticks per Frame):"))
            self.sld_speed = QSlider(Qt.Horizontal)
            self.sld_speed.setRange(50, 1000)
            self.sld_speed.setValue(200)
            self.sld_speed.valueChanged.connect(self.on_speed_changed)
            box.addWidget(self.sld_speed)
            self.lbl_speed = QLabel("ticks_per_frame: 200")
            box.addWidget(self.lbl_speed)

            reset_group = QGroupBox("World")
            reset_layout = QHBoxLayout(reset_group)
            self.btn_reset = QPushButton("Reset")
            self.btn_new_seed = QPushButton("New Seed")
            self.btn_reset.clicked.connect(self.reset_same_seed)
            self.btn_new_seed.clicked.connect(self.reset_new_seed)
            reset_layout.addWidget(self.btn_reset)
            reset_layout.addWidget(self.btn_new_seed)
            box.addWidget(reset_group)

            snap_group = QGroupBox("Persistence")
            snap_layout = QHBoxLayout(snap_group)
            self.btn_snap = QPushButton("Snapshot Now")
            self.btn_snap.clicked.connect(self.snapshot_now)
            snap_layout.addWidget(self.btn_snap)

            self.spn_snap_every = QSpinBox()
            self.spn_snap_every.setRange(0, 200000)
            self.spn_snap_every.setValue(self.cfg.snapshot_every_ticks)
            self.spn_snap_every.valueChanged.connect(self.on_snap_every_changed)
            snap_layout.addWidget(QLabel("every"))
            snap_layout.addWidget(self.spn_snap_every)
            snap_layout.addWidget(QLabel("ticks"))
            box.addWidget(snap_group)

            overlay_group = QGroupBox("Overlays")
            overlay_layout = QVBoxLayout(overlay_group)
            self.cb_proto = QCheckBox(f"S > {self.cfg.proto_S_threshold} (proto)")
            self.cb_alive = QCheckBox(f"S > {self.cfg.alive_S_threshold} (alive-cand)")
            self.cb_showM = QCheckBox("Show M")
            self.cb_proto.setChecked(True)
            self.cb_alive.setChecked(False)
            self.cb_showM.setChecked(bool(self.cfg.enable_M))
            for cb in [self.cb_proto, self.cb_alive, self.cb_showM]:
                cb.stateChanged.connect(lambda: self.refresh_plots())
                overlay_layout.addWidget(cb)
            box.addWidget(overlay_group)

            det_group = QGroupBox("Detection")
            det_layout = QVBoxLayout(det_group)
            self.cb_detect = QCheckBox("Enable Replicator Detection")
            self.cb_detect.setChecked(self.cfg.detect_enabled)
            self.cb_detect.stateChanged.connect(self.on_detect_toggle)
            det_layout.addWidget(self.cb_detect)
            self.lbl_detect = QLabel("clusters: 0 | replications: 0")
            det_layout.addWidget(self.lbl_detect)
            box.addWidget(det_group)

            # -------- Phase toggles --------
            phase_group = QGroupBox("Phase Toggles")
            phase_layout = QVBoxLayout(phase_group)
            self.cb_enable_m = QCheckBox("Enable M / Mutation Layer")
            self.cb_enable_m.setChecked(self.cfg.enable_M)
            self.cb_enable_m.stateChanged.connect(self.on_m_toggle)
            phase_layout.addWidget(self.cb_enable_m)
            box.addWidget(phase_group)

            # -------- Hunter --------
            hunt_group = QGroupBox("Hunter Agent")
            hunt_layout = QVBoxLayout(hunt_group)

            self.btn_hunt = QPushButton("HUNT: OFF")
            self.btn_hunt.clicked.connect(self.toggle_hunter)
            hunt_layout.addWidget(self.btn_hunt)

            row1 = QHBoxLayout()
            row1.addWidget(QLabel("target_survival"))
            self.spn_target_surv = QSpinBox()
            self.spn_target_surv.setRange(100, 2_000_000)
            self.spn_target_surv.setValue(self.cfg.hunter_target_survival_ticks)
            self.spn_target_surv.valueChanged.connect(self.on_hunter_params)
            row1.addWidget(self.spn_target_surv)
            hunt_layout.addLayout(row1)

            row2 = QHBoxLayout()
            row2.addWidget(QLabel("max_ticks/seed"))
            self.spn_max_ticks = QSpinBox()
            self.spn_max_ticks.setRange(1_000, 5_000_000)
            self.spn_max_ticks.setValue(self.cfg.hunter_max_ticks_per_seed)
            self.spn_max_ticks.valueChanged.connect(self.on_hunter_params)
            row2.addWidget(self.spn_max_ticks)
            hunt_layout.addLayout(row2)

            row3 = QHBoxLayout()
            row3.addWidget(QLabel("batch"))
            self.spn_batch = QSpinBox()
            self.spn_batch.setRange(1, 200)
            self.spn_batch.setValue(self.cfg.hunter_seeds_per_batch)
            self.spn_batch.valueChanged.connect(self.on_hunter_params)
            row3.addWidget(self.spn_batch)
            hunt_layout.addLayout(row3)

            self.lbl_hunt_status = QLabel("Hunter: idle")
            self.lbl_hunt_stats = QLabel("tested: 0 | found: 0")
            hunt_layout.addWidget(self.lbl_hunt_status)
            hunt_layout.addWidget(self.lbl_hunt_stats)
            box.addWidget(hunt_group)

            # -------- Inbox --------
            inbox_group = QGroupBox("Anomaly Inbox (double-click to load)")
            inbox_layout = QVBoxLayout(inbox_group)

            self.txt_filter = QLineEdit()
            self.txt_filter.setPlaceholderText("filter: seed / reason / survived...")
            self.txt_filter.textChanged.connect(self.scan_inbox)
            inbox_layout.addWidget(self.txt_filter)

            self.lst_inbox = QListWidget()
            self.lst_inbox.itemDoubleClicked.connect(self.on_inbox_double_click)
            inbox_layout.addWidget(self.lst_inbox)

            self.btn_refresh_inbox = QPushButton("Refresh Inbox")
            self.btn_refresh_inbox.clicked.connect(self.scan_inbox)
            inbox_layout.addWidget(self.btn_refresh_inbox)

            self.lbl_inbox_detail = QLabel("Select an item…")
            self.lbl_inbox_detail.setWordWrap(True)
            inbox_layout.addWidget(self.lbl_inbox_detail)

            box.addWidget(inbox_group)

            # -------- Footer --------
            self.lbl_tick = QLabel("Tick: 0")
            self.lbl_stats = QLabel("Emax: 0.00 | R1max: 0.00 | Smax: 0.00 | Mμ: 0.00")
            self.lbl_hash = QLabel("hash: -")
            box.addWidget(self.lbl_tick)
            box.addWidget(self.lbl_stats)
            box.addWidget(self.lbl_hash)

            box.addStretch(1)

        # -------------------------
        # Plots
        # -------------------------
        def _build_plots(self) -> QWidget:
            if self.plot_backend == "pyqtgraph":
                # Structural UI change: image panel rendering is now data-item based,
                # so refreshes update only image buffers (faster than full-canvas redraw).
                self.pg_layout = pg.GraphicsLayoutWidget()
                # Structural UI change: dark-mode plot canvas and fixed-grid behavior improve
                # visibility and prevent accidental panning/zooming interactions.
                self.pg_layout.setBackground("k")

                title_style = {"color": "#E6E6E6"}
                self.plotE = self.pg_layout.addPlot(0, 0)
                self.plotR = self.pg_layout.addPlot(0, 1)
                self.plotS = self.pg_layout.addPlot(0, 2)
                self.plotM = self.pg_layout.addPlot(0, 3)
                self.plotE.setTitle("E (Energy)", **title_style)
                self.plotR.setTitle("R1 (Raw)", **title_style)
                self.plotS.setTitle("S (Structure)", **title_style)
                self.plotM.setTitle("M (Proto-Genom)", **title_style)

                for plot in [self.plotE, self.plotR, self.plotS, self.plotM]:
                    plot.setAspectLocked(True)
                    plot.hideAxis("left")
                    plot.hideAxis("bottom")
                    plot.invertY(True)
                    plot.setMouseEnabled(x=False, y=False)
                    plot.hideButtons()

                self.imE = pg.ImageItem(axisOrder="row-major")
                self.imR = pg.ImageItem(axisOrder="row-major")
                self.imS = pg.ImageItem(axisOrder="row-major")
                self.imM = pg.ImageItem(axisOrder="row-major")

                # Structural UI change: dedicated LUTs increase channel contrast readability.


                self.plotE.addItem(self.imE)
                self.plotR.addItem(self.imR)
                self.plotS.addItem(self.imS)
                self.plotM.addItem(self.imM)

                # Structural UI change: contour overlays become explicit isocurve items
                # on top of S, preserving proto/alive visual toggles.
                self._proto_curve = pg.IsocurveItem(level=0.5, pen=pg.mkPen("c", width=1))
                self._alive_curve = pg.IsocurveItem(level=0.5, pen=pg.mkPen("lime", width=1))
                self.plotS.addItem(self._proto_curve)
                self.plotS.addItem(self._alive_curve)
                self._proto_curve.hide()
                self._alive_curve.hide()

                return self.pg_layout

            self.fig = Figure(figsize=(13, 4), tight_layout=True)
            self.canvas = FigureCanvas(self.fig)

            self.axE = self.fig.add_subplot(1, 4, 1)
            self.axR = self.fig.add_subplot(1, 4, 2)
            self.axS = self.fig.add_subplot(1, 4, 3)
            self.axM = self.fig.add_subplot(1, 4, 4)

            for ax, title in [
                (self.axE, "E (Energy)"),
                (self.axR, "R1 (Raw)"),
                (self.axS, "S (Structure)"),
                (self.axM, "M (Proto-Genom)"),
            ]:
                ax.set_title(title)
                ax.set_xticks([])
                ax.set_yticks([])

            self.imE = self.axE.imshow(self.core.E, cmap="magma", vmin=0, vmax=2)
            self.imR = self.axR.imshow(self.core.R1, cmap="viridis", vmin=0, vmax=1)
            self.imS = self.axS.imshow(self.core.S, cmap="magma", vmin=0, vmax=1)
            self.imM = self.axM.imshow(self.core.M, cmap="viridis", vmin=0, vmax=1)

            self._cont_proto = None
            self._cont_alive = None
            return self.canvas

        # -------------------------
        # Sim controls
        # -------------------------
        def toggle_start_pause(self):
            self.sim_running = not self.sim_running
            if self.sim_running:
                self.btn_start.setText("PAUSE")
                if not self.timer.isActive():
                    self.timer.start()
            else:
                self.btn_start.setText("START")

        def on_speed_changed(self, val: int):
            self.lbl_speed.setText(f"ticks_per_frame: {val}")

        def on_snap_every_changed(self, val: int):
            self.cfg.snapshot_every_ticks = int(val)

        def on_detect_toggle(self):
            self.cfg.detect_enabled = self.cb_detect.isChecked()

        def step_n(self, n: int):
            self.sim_running = False
            self.btn_start.setText("START")
            for _ in range(n):
                self.core.step()
                self._auto_snapshot_tick()
            self.post_step_jobs()
            self.refresh_plots()

        def reset_same_seed(self):
            self.sim_running = False
            self.btn_start.setText("START")
            self.core.reset(same_seed=True)
            self._fp_history.clear()
            self._replication_events = 0
            self.eventlog.write({"t": self.core.tick, "type": "reset", "same_seed": True, "seed": self.core.seed})
            self.refresh_plots()

        def reset_new_seed(self):
            self.sim_running = False
            self.btn_start.setText("START")
            self.core.reset(same_seed=False)
            self._fp_history.clear()
            self._replication_events = 0
            self.eventlog.write({"t": self.core.tick, "type": "reset", "same_seed": False, "seed": self.core.seed})
            self.refresh_plots()

        def snapshot_now(self):
            snap = self.core.snapshot()
            path = self.run_dir / "snapshots" / f"tick_{snap.tick:09d}.npz"
            snap.save_npz(path)
            self.eventlog.write({"t": self.core.tick, "type": "snapshot", "path": str(path)})

        def on_m_toggle(self):
            self.cfg.enable_M = self.cb_enable_m.isChecked()
            if not self.cfg.enable_M:
                self.core.M.fill(0.0)
            self.refresh_plots()

        def on_timer(self):
            if not self.sim_running:
                return
            n = int(self.sld_speed.value())
            for _ in range(n):
                self.core.step()
                self._auto_snapshot_tick()
            self.post_step_jobs()
            self.refresh_plots()

        def _auto_snapshot_tick(self):
            every = int(self.cfg.snapshot_every_ticks)
            if every > 0 and (self.core.tick % every == 0):
                self.snapshot_now()

        def post_step_jobs(self):
            self._frame_counter += 1
            clusters = 0
            if self.cfg.detect_enabled and (self._frame_counter % max(1, self.cfg.detect_every_n_frames) == 0):
                labels, n_clusters = label_clusters_bool(
                    self.core.S > self.cfg.proto_S_threshold,
                    moore=(self.cfg.neighborhood == "moore"),
                    torus=True,
                )
                clusters = n_clusters
                fps = compute_fingerprints(self.core, labels, n_clusters)
                rep = match_replications(
                    self._fp_history,
                    fps,
                    t_now=self.core.tick,
                    dt_max=self.cfg.detect_match_dt_ticks,
                    sim_thr=self.cfg.detect_similarity_threshold,
                )
                if rep:
                    self._replication_events += len(rep)
                    self.eventlog.write({
                        "t": self.core.tick,
                        "type": "replication_detected",
                        "count": len(rep),
                        "pairs": rep,
                    })
                self._fp_history = fps
            self.lbl_detect.setText(f"clusters: {clusters} | replications: {self._replication_events}")

        def refresh_plots(self):
            if self.plot_backend == "pyqtgraph":
                v_max_E = max(1e-6, float(self.core.E.max()))
                v_max_R1 = max(1e-6, float(self.core.R1.max()))
                v_max_S = max(1e-6, float(self.core.S.max()))

                self.imE.setImage(self.core.E, autoLevels=False)
                self.imR.setImage(self.core.R1, autoLevels=False)
                self.imS.setImage(self.core.S, autoLevels=False)
                self.imM.setImage(self.core.M, autoLevels=False)

                self.imE.setLevels((0.0, v_max_E))
                self.imR.setLevels((0.0, v_max_R1))
                self.imS.setLevels((0.0, v_max_S))
                self.imM.setLevels((0.0, 1.0))

                self.plotM.setVisible(self.cb_showM.isChecked())

                if self.cb_proto.isChecked():
                    self._proto_curve.setData((self.core.S > self.cfg.proto_S_threshold).astype(np.float32))
                    self._proto_curve.show()
                else:
                    self._proto_curve.hide()

                if self.cb_alive.isChecked():
                    self._alive_curve.setData((self.core.S > self.cfg.alive_S_threshold).astype(np.float32))
                    self._alive_curve.show()
                else:
                    self._alive_curve.hide()

                self.lbl_tick.setText(f"Tick: {self.core.tick}")
                self.lbl_stats.setText(
                    f"Emax: {self.core.E.max():.3f} | R1max: {self.core.R1.max():.3f} | "
                    f"Smax: {self.core.S.max():.3f} | Mμ: {self.core.M.mean():.3f}"
                )
                self.lbl_hash.setText(f"hash: {self.core.state_hash()}")
                return

            self.imE.set_data(self.core.E)
            self.imR.set_data(self.core.R1)
            self.imS.set_data(self.core.S)
            self.imM.set_data(self.core.M)
            self.imM.set_visible(self.cb_showM.isChecked())
            self.axM.set_visible(self.cb_showM.isChecked())

            # autoscale image ranges for readability
            self.imE.set_clim(0.0, max(1e-6, float(self.core.E.max())))
            self.imR.set_clim(0.0, max(1e-6, float(self.core.R1.max())))
            self.imS.set_clim(0.0, max(1e-6, float(self.core.S.max())))
            self.imM.set_clim(0.0, 1.0)

            # clear old overlays safely
            for cont_name in ["_cont_proto", "_cont_alive"]:
                cont = getattr(self, cont_name, None)
                if cont is not None:
                    for coll in cont.collections:
                        coll.remove()
                    setattr(self, cont_name, None)

            if self.cb_proto.isChecked():
                self._cont_proto = self.axS.contour(
                    (self.core.S > self.cfg.proto_S_threshold).astype(np.uint8),
                    levels=[0.5],
                    colors=["cyan"],
                    linewidths=0.7,
                )
            if self.cb_alive.isChecked():
                self._cont_alive = self.axS.contour(
                    (self.core.S > self.cfg.alive_S_threshold).astype(np.uint8),
                    levels=[0.5],
                    colors=["lime"],
                    linewidths=0.9,
                )

            self.lbl_tick.setText(f"Tick: {self.core.tick}")
            self.lbl_stats.setText(
                f"Emax: {self.core.E.max():.3f} | R1max: {self.core.R1.max():.3f} | "
                f"Smax: {self.core.S.max():.3f} | Mμ: {self.core.M.mean():.3f}"
            )
            self.lbl_hash.setText(f"hash: {self.core.state_hash()}")
            self.canvas.draw_idle()

        def on_hunter_params(self):
            self.hunter.target_survival = int(self.spn_target_surv.value())
            self.hunter.max_ticks = int(self.spn_max_ticks.value())
            self.hunter.batch = int(self.spn_batch.value())

        def toggle_hunter(self):
            if self.hunter.isRunning():
                self.hunter.stop()
                self.hunter.wait(500)
                self.btn_hunt.setText("HUNT: OFF")
                self.cfg.hunter_enabled = False
                return
            self.on_hunter_params()
            self.hunter.start()
            self.btn_hunt.setText("HUNT: ON")
            self.cfg.hunter_enabled = True

        def on_hunter_status(self, msg: str):
            self.lbl_hunt_status.setText(msg)
            if "stopped" in msg.lower() and self.btn_hunt.text() != "HUNT: OFF":
                self.btn_hunt.setText("HUNT: OFF")

        def on_hunter_found(self, summary_path: str):
            self.scan_inbox()
            self.lbl_hunt_status.setText(f"Hunter found anomaly: {summary_path}")

        def on_hunter_stats(self, tested: int, found: int):
            self.lbl_hunt_stats.setText(f"tested: {tested} | found: {found}")

        def scan_inbox(self):
            base = Path(self.cfg.hunter_anomalies_dir)
            ensure_dir(base)
            q = self.txt_filter.text().strip().lower() if hasattr(self, "txt_filter") else ""

            self.lst_inbox.clear()
            items = []
            for summary in sorted(base.glob('*/summary.json'), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(summary.read_text(encoding='utf-8'))
                except Exception:
                    continue
                text = (
                    f"seed={data.get('seed')} | reason={data.get('reason')} | "
                    f"surv={data.get('survival_ticks')} | tick={data.get('end_tick')}"
                )
                if q and q not in text.lower():
                    continue
                items.append((text, str(summary)))

            for text, path in items:
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, path)
                self.lst_inbox.addItem(it)

            if not items:
                self.lbl_inbox_detail.setText("Inbox empty.")

        def on_inbox_double_click(self, item):
            path = Path(str(item.data(Qt.UserRole)))
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                end_path = data.get('paths', {}).get('end')
                if not end_path:
                    return
                snap = Snapshot.load_npz(Path(end_path))
                self.core.load_snapshot(snap)
                self.cfg = self.core.cfg
                self.cb_enable_m.setChecked(bool(self.cfg.enable_M))
                self._fp_history.clear()
                self._replication_events = 0
                self.eventlog.write({"t": self.core.tick, "type": "load_anomaly", "summary": str(path)})
                self.lbl_inbox_detail.setText(
                    f"Loaded seed={data.get('seed')} reason={data.get('reason')} "
                    f"survival={data.get('survival_ticks')}"
                )
                self.refresh_plots()
            except Exception as exc:
                self.lbl_inbox_detail.setText(f"Failed to load: {exc}")

    app = QApplication.instance() or QApplication(sys.argv)
    w = GenesisCockpit()
    w.resize(1600, 700)
    w.show()
    app.exec()


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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genesis Monolith")
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--ticks", type=int, default=20000)
    p.add_argument("--snapshot-every", type=int, default=2000)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = WorldConfig(size=int(args.size), snapshot_every_ticks=int(args.snapshot_every))

    run_dir = ensure_dir(Path(cfg.out_dir) / now_id())
    (run_dir / "snapshots").mkdir(parents=True, exist_ok=True)

    if args.headless:
        run_headless(cfg, seed=int(args.seed), ticks=int(args.ticks), run_dir=run_dir)
    else:
        run_ui(cfg, seed=int(args.seed), run_dir=run_dir)


if __name__ == "__main__":
    main()
