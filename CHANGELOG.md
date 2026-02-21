# Changelog



## [0.0.7] - 2026-02-21
### Fixed
- Synchronized `post_step_jobs()` replication detection call with `match_replications(fps_now, history, dt_ticks, sim_thresh)` by removing stale kwargs (`t_now`, `dt_max`, `sim_thr`) and aligning argument order/names.

## [0.0.6] - 2026-02-21
### Added
- Added a standalone `tests/` suite with deterministic core checks (`test_determinism.py`), mass bounds sanity checks (`test_mass.py`), snapshot roundtrip validation (`test_snapshot.py`), and NaN/Inf stability checks (`test_no_nan.py`).
- Added `tests/conftest.py` to ensure stable imports from the repository root when running pytest from different working directories.

## [0.0.5] - 2026-02-21
### Changed
- Hunter tested-seed persistence now uses a compact SQLite cache (`runs/anomalies/tested_seeds.sqlite3`) with in-place updates (`INSERT OR IGNORE`) instead of a growing text log.
- Added one-time migration from legacy `tested_seeds.log` to SQLite and automatic cleanup of the old log file to reduce disk usage.

## [0.0.4] - 2026-02-20
### Changed
- Hunter now persists a `tested_seeds.log` file in `runs/anomalies` and skips already-tested seeds across runs to avoid re-testing known dead/processed seeds.

## [0.0.3] - 2026-02-19
### Changed
- Updated PyQtGraph cockpit plots to dark-mode styling with high-contrast titles and fixed (non-pan/zoom) grid interaction.
- Added per-channel colormap LUTs (inferno/viridis/magma/winter) and explicit dynamic level scaling for E/R1/S to avoid blacked-out frames and improve data visibility.

## [0.0.2] - 2026-02-19
### Changed
- UI plotting now prefers PyQtGraph for high-frequency cockpit refreshes and falls back to Matplotlib if PyQtGraph is unavailable.
- Reworked the plotting pipeline to support backend-specific refresh logic while preserving overlays, M-visibility toggles, and status readouts.

## [0.0.1] - 2026-02-19
### Fixed
- Recovered `Monolith.py` from a truncated `snapshot_now()` method that caused a hard syntax failure and prevented startup.
- Restored missing UI runtime methods (timer loop, plotting refresh, inbox loading, hunter callbacks, and snapshot automation) so the monolith can run again.

### Changed
- Phase-0 defaults now start with mutation/proto-genome (`enable_M`) disabled.
- Added a GUI toggle for enabling/disabling the M/mutation layer at runtime.
- Kept Hunter agent runtime-toggle behavior and default-off startup behavior aligned with the recovery scope.
