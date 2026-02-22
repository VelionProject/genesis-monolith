from __future__ import annotations

from dataclasses import dataclass


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
