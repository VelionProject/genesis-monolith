# Changelog


## [0.0.6] - 2026-02-21
### Changed
- UI sidebar is now wrapped in a vertical `QScrollArea` to prevent clipping when window height is limited and to keep future control additions accessible.
- Added a persistent bottom toggle button to hide/show the sidebar and restore a readable splitter ratio when re-enabled.
- Added sidebar/plot minimum widths and splitter default sizes to improve resize/fullscreen stability.

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
