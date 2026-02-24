# Changelog


## [0.0.13] - 2026-02-24
### Changed
- Reworked cockpit sidebar layout sizing so the panel keeps a wider readable width after resize/show operations, reducing right-edge clipping of controls.
- Enabled horizontal fallback scrolling in the sidebar and improved control readability with wrapped status labels and cleaner section separators.

## [0.0.12] - 2026-02-24
### Changed
- Updated cockpit sidebar UX to include panel visibility toggles and an in-UI chemistry semantics legend, while preserving Hunter/Inbox controls as toggleable sections.
- Renamed key UI labels to domain-oriented wording (`R1` as Boden/Substrat and `M` as Membran) in plot titles and runtime stats for better interpretability.

## [0.0.11] - 2026-02-23
### Added
- Added a no-code chemistry/physics notation analysis document that maps `E`, `R1`, `R2`, `S`, `T`, `M` to simulation semantics and proposes a visualization grammar for future cockpit design (`docs/chemistry-physics-analysis-model.md`).


## [0.0.10] - 2026-02-22
### Added
- Added a runtime Activity Glow overlay (`ΔS`) in the cockpit with a sidebar toggle and activity status label (`stable` / `moderate` / `active`) so simulation dynamics are visible directly in the UI.
- Added deterministic helper utilities (`compute_activity_map`, `classify_activity_level`) and unit tests for normalization and threshold classification.

## [0.0.9] - 2026-02-22
### Added
- Added a no-code product/UX specification for visual dynamics overlays (activity glow, diffusion flow vectors, and mutation stress monitor), including default thresholds, compositing order, and rollout phases (`docs/visual-dynamics-overlay-spec.md`).


## [0.0.8] - 2026-02-21
### Changed
- Auto-run speed slider now supports `1..1000` ticks/frame (instead of starting at 50) and defaults to `1` tick/frame for safer low-load startup behavior.

## [0.0.7] - 2026-02-21
### Fixed
- Stabilized UI auto-run by adding a re-entrancy guard around the timer tick handler so expensive frames cannot overlap and freeze/crash the cockpit update loop.

### Added
- Extended the sidebar Step controls with a custom tick input (`QSpinBox`) and dedicated Run button for manual stepping beyond ×1/×10/×100 presets.

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
