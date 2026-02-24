# Chemistry/Physics Notation Analysis (`R1`, `R2`, `E`, `S`, `T`, `M`)

## Goal
Create a shared interpretation of the simulation symbols so the model can be visualized in a consistent, logically readable way before making UI/code changes.

## Symbol semantics from the current core

### `E` (Energy field)
- **Role:** global fuel/input potential for transformations and structure maintenance.
- **Dynamics:** receives continuous inflow from energy sources (`hotspots` or `gradient`), diffuses, decays, and is consumed by reactions and maintenance.
- **Visualization meaning:** where reaction potential exists and where starvation risk is rising.

### `R1` (Resource/precursor pool)
- **Role:** precursor material that can be activated when enough `E` is present.
- **Dynamics:** converted to `R2` via `k1`, regenerated from `S` via `k3`, diffuses slowly and decays lightly.
- **Visualization meaning:** "feedstock availability" per cell.

### `R2` (Activated intermediate)
- **Role:** transient intermediate between precursor (`R1`) and structure (`S`).
- **Dynamics:** created from `R1 + E`, consumed into `S` via `k2`, optionally consumed into `T` via `k4`.
- **Visualization meaning:** short-lived conversion pressure / active chemistry front.

### `S` (Structure / self-maintained organization)
- **Role:** persistence/order variable (what behaves most like "living structure" in this model).
- **Dynamics:** created from `R2 + E`, partially recycled back to `R1`, requires ongoing `E` maintenance, can bleed under starvation.
- **Visualization meaning:** structural footprint, viability, cluster identity.

### `T` (Byproduct / optional sink channel)
- **Role:** optional side-product channel enabled by `enable_T` and `k4`.
- **Dynamics:** produced from `R2 + E`, diffuses and decays.
- **Visualization meaning:** inefficiency footprint or alternate pathway intensity.

### `M` (Proto-genome / strategy field)
- **Role:** adaptive control layer that modulates trade-offs (conversion vs maintenance).
- **Dynamics:** can initialize with baseline/noise/islands, mutate during growth, optionally diffuse/decay; scales `k2` and maintenance cost.
- **Visualization meaning:** local adaptation strategy and heritable heterogeneity.

## Reaction interpretation (conceptual)
- `R1 + E -> R2` (`k1`): activation step.
- `R2 + E -> S` (`k2`/`k2_eff`): structure-building step.
- `S -> R1` (`k3`): recycling/backflow.
- `R2 + E -> T` (`k4`, optional): side-path/byproduct.

This is effectively an **energy-coupled reaction-diffusion ecology** with optional adaptive parameterization (`M`).

## Physics interpretation
- **Space model:** 2D toroidal grid.
- **Transport:** Laplacian diffusion, locally damped by existing `S`.
- **Forcing:** external energy map (`energy_mask`).
- **Dissipation:** per-field decay and bounded clamping.
- **Stability guards:** non-negative clamps + finite-value reset strategy.

## Proposed visualization grammar (no code)

### 1) Core channels (always visible)
- `S` as primary occupancy/structure heatmap.
- `E` as underlay or contour bands for energy landscape.
- `R2` as activity accent layer (highlights active conversion fronts).

### 2) Secondary channels (toggleable)
- `R1` to inspect precursor availability bottlenecks.
- `T` to inspect side-path losses.
- `M` to inspect adaptation/genotype landscape.

### 3) Derived overlays (recommended)
- **Viability risk:** high `S` + low `E` zones.
- **Conversion efficiency:** ratio-like indicator based on `S` growth versus `E` draw.
- **Strategy map:** regions where `M` pushes toward fast conversion (`k2` up) vs cheap maintenance.
- **Cluster lineage hints:** fingerprints over `S` clusters + `M` variance.

## Naming normalization proposal
To reduce ambiguity in UI labels and docs:
- `E` -> "Energy"
- `R1` -> "Precursor"
- `R2` -> "Activated Intermediate"
- `S` -> "Structure"
- `T` -> "Byproduct"
- `M` -> "Genome Bias"

Keep short symbols for internal plots/tooltips, but display full names in legends and inspector panels.

## Minimal decision set before implementation
1. Which field is the **primary truth view**? (recommended: `S`)
2. Should `T` be treated as inefficiency or useful signal?
3. Is `M` user-facing by default or expert-only?
4. Which 2–3 derived KPIs should be always visible in the cockpit summary?

Once these are fixed, UI design and data contracts can remain stable even when equations evolve.
