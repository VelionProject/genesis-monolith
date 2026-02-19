# Changelog

## [0.0.1] - 2026-02-19
### Fixed
- Recovered `Monolith.py` from a truncated `snapshot_now()` method that caused a hard syntax failure and prevented startup.
- Restored missing UI runtime methods (timer loop, plotting refresh, inbox loading, hunter callbacks, and snapshot automation) so the monolith can run again.

### Changed
- Phase-0 defaults now start with mutation/proto-genome (`enable_M`) disabled.
- Added a GUI toggle for enabling/disabling the M/mutation layer at runtime.
- Kept Hunter agent runtime-toggle behavior and default-off startup behavior aligned with the recovery scope.

## [0.0.2] - 2026-02-19
### Changed
- Aligned `Monolith.py` top-level run instructions with the current executable filename and currently supported CLI flags.
- Removed a contradictory internal comment that claimed there was no CLI path.
- Simplified startup config by removing a redundant runtime override for `enable_M` (now sourced from `WorldConfig` defaults).
