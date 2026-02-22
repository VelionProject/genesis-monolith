from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .core import PhysicsCore


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


def compute_activity_map(prev_state: np.ndarray, curr_state: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Normalized absolute per-cell delta map in [0,1] for visual activity overlays."""
    delta = np.abs(curr_state - prev_state)
    scale = float(np.max(delta))
    if scale <= eps:
        return np.zeros_like(delta, dtype=np.float32)
    return (delta / scale).astype(np.float32)


def classify_activity_level(score: float) -> str:
    """Three-band activity label used by the cockpit summary UI."""
    if score < 0.08:
        return "stable"
    if score < 0.35:
        return "moderate"
    return "active"
