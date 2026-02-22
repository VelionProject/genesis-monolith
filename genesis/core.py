from __future__ import annotations

import hashlib
from dataclasses import asdict

import numpy as np

from .config import WorldConfig
from .persistence import Snapshot


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
