# Visual Dynamics Overlay Specification (No-Code Design)

## Purpose
This design note translates the product idea into an implementation-ready specification for three visualization layers:

1. **Activity Layer (Glow):** highlights active growth/reaction zones.
2. **Flow Vectors:** communicates local diffusion direction and intensity.
3. **Stress Monitor:** exposes mutation stress zones under energy scarcity.

The goal is to improve interpretation of system dynamics without changing simulation semantics.

## Scope and non-goals
### In scope
- UI/UX behavior and metric mapping for overlays.
- Thresholding and calibration strategy.
- Performance and readability constraints.
- Rollout order and acceptance criteria.

### Out of scope
- Simulation algorithm changes.
- Backend refactors or data-model migration.

## Layer 1: Activity Layer ("The Glow")
### Signal definition
- Per-cell activity score:
  - `A_t = normalize(abs(state_t - state_(t-1)))`
- Suggested default source channels:
  - primary: growth-relevant chemistry channel(s)
  - optional blend: energy change magnitude

### Rendering behavior
- Overlay color: cyan/blue-violet family (reserved away from stress red).
- Opacity: proportional to `A_t` with soft clamp.
- Pulse: subtle temporal pulse for cells where `A_t` exceeds trigger.
- Noise gate: do not render if `A_t < T_activity_min`.

### Defaults
- `T_activity_min = 0.08`
- `T_activity_high = 0.35`
- UI label mapping:
  - `A_t < 0.08` → "stable"
  - `0.08 .. 0.35` → "moderate"
  - `> 0.35` → "active"

## Layer 2: Flow Vectors (Diffusion Tails)
### Signal definition
- Use gradient-derived direction from energy field `E`:
  - direction vector `v = -grad(E)`
- Magnitude:
  - `|v|` normalized into display range

### Rendering behavior
- Glyph style: short tapered streaks (comet-tail style), low alpha.
- Density control:
  - draw every `n`th cell (default `n = 3`) for readability.
- Level-of-detail:
  - hidden in compact mode;
  - shown in "Detail" mode or above zoom threshold.

### Defaults
- `vector_alpha = 0.25`
- `vector_min_mag = 0.05`
- `vector_max_len_px = 10`

## Layer 3: Stress Monitor
### Signal definition
- Use mutation stress proxy from model surface:
  - baseline variable: `M_mut_stress_factor`
- Optional composite:
  - stress = high mutation pressure + low local energy condition

### Rendering behavior
- Heatmap palette: dark red → bright red (avoid orange overlap with warnings).
- Blend mode: additive with capped max opacity to preserve base map visibility.
- Legend required: always visible in detail mode.

### Defaults
- `T_stress_warn = 0.45`
- `T_stress_critical = 0.70`
- severity labels:
  - `< 0.45` normal
  - `0.45 .. 0.70` elevated
  - `> 0.70` critical

## Combined compositing and priority
To avoid visual overload:
1. Stress layer draws first (background heat tint, low alpha).
2. Activity glow draws second (mid alpha, pulse allowed).
3. Flow vectors draw last (thin, sparse, low alpha).

UI toggles should allow independent enable/disable per layer.

## UX controls
### Compact mode (default)
- Show activity badge and small trend sparkline.
- Show glow layer only.

### Detail mode
- Enable vector field and stress heatmap.
- Show legends, thresholds, and sampling controls.

### Accessibility
- Never encode status by color alone.
- Always pair with text labels (`stable`, `moderate`, `active`, `critical`).

## Performance budget
- Overlay rendering must not reduce simulation FPS by more than 15% in default viewport.
- Use decimation for vectors and capped pulse animation frequency.
- Cache color lookups and reuse buffers where possible.

## Calibration workflow
1. Capture baseline runs (quiet, moderate, turbulent).
2. Compute percentile bands for `A_t` and stress metrics.
3. Tune thresholds so labels align with observed behavior.
4. Freeze defaults and keep runtime overrides for expert users.

## Rollout plan
1. **Phase 1 (MVP):** Activity glow + status labels + sparkline.
2. **Phase 2:** Stress monitor heatmap + legend.
3. **Phase 3:** Flow vectors with detail-mode gating.

## Acceptance criteria
- Users can identify where activity is currently happening in under 2 seconds.
- Users can distinguish stable vs turbulent phases from the trend view.
- Users can detect high-stress regions without obscuring base simulation data.
- Overlay stack remains readable on small screens.

## Risks and mitigations
- Risk: noisy inputs produce constant glow.
  - Mitigation: smoothing + minimum activity threshold.
- Risk: vector clutter.
  - Mitigation: sampling, zoom gating, detail mode only.
- Risk: red stress map dominates all visuals.
  - Mitigation: alpha cap and contrast checks against glow palette.
