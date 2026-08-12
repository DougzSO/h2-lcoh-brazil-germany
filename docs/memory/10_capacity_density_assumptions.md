# 10 — Capacity Density Assumptions

**Naming note**: this task's own instructions named this file
`05_capacity_density_assumptions.md`, but `05` is already
`05_economic_model.md` in this directory (see `README.md`'s index) —
numbered `10` instead to avoid overwriting/colliding with that existing
file. Cross-referenced from `README.md`, `04_spatial_methodology.md`, and
`09_methodology_assumptions.md`.

This file documents the literature review and study-specific decisions
behind `config.technologies.<solar_pv|onshore_wind>.<brazil|germany>.
capacity_density_mw_per_km2` in `scenario_params.yaml` — the installable
capacity per unit of suitable land area used by `potential/h2_potential.py`
(`installable_capacity_mw = suitable_area_km2 * power_density_mw_per_km2`).

## Migration summary

Previously a single, non-region-specific scalar:
`renewables.<solar_pv|onshore_wind>.power_density_w_per_m2` (30.0 solar,
8.0 wind — numerically equal to MW/km² for this unit pair, per the old
code's own comment, but stored in W/m²). This migration replaces both
scalars with a literature-validated `{min, baseline, max}` range under
`technologies.<tech>.<region>.capacity_density_mw_per_km2`, already
expressed directly in MW/km² (no more W/m²→MW/km² conversion step in
`h2_potential.py` — removed along with the scalar it existed for).

Capacity density is region-specific in the new schema's *structure* (each
of `brazil`/`germany` carries its own `RangeParam`), but not in its
*values* — both regions currently use the same NREL fixed-tilt /
standard-turbine-spacing figures per technology, a deliberate
study-specific choice (installable density depends on land-use
intensity/turbine engineering, not on regional resource quality the way
capacity factor does), not a schema limitation. A future revision could
diverge them without any code change.

## Literature Review Summary

### Solar PV (Utility-Scale)
- **Fixed-tilt:** 43–87 MW/km²
- **Single-axis tracking:** 29–59 MW/km²
- **Sources:** NREL empirical studies, ArcGIS satellite imagery

### Onshore Wind
- **Conservative:** 4.1–13.7 MW/km²
- **Modern high-density:** 19.8–20.5 MW/km²
- **Sources:** Technical potential models, convex-hull area

## Study-Specific Decisions

| Tech  | Range          | Baseline    | Source |
|-------|----------------|-------------|--------|
| Solar | 43–60 MW/km²   | 51.5 MW/km² | NREL fixed-tilt |
| Wind  | 4.1–13.7 MW/km²| 8.9 MW/km²  | Standard spacing |

### Literature basis and citations

**Solar PV:** the capacity density range of 43–60 MW/km² (baseline
51.5 MW/km²) is derived from empirical studies of utility-scale fixed-tilt
PV installations in the United States (Ong et al., 2013; Bolinger and
Bolinger, 2022) and the meta-analysis of power densities across
generation technologies by Van Zalk and Behrens (2018). The baseline of
51.5 MW/km² represents the midpoint of the empirically-observed range for
ground-mounted utility-scale systems, and is applied uniformly to both
regions since installable density follows from panel packing and inverter
configuration rather than from regional resource quality (see the
"Migration summary" section above).

**Onshore wind:** the capacity density range of 4.1–13.7 MW/km² (baseline
8.9 MW/km²) is derived from Archer et al. (2019), Van Zalk and Behrens
(2018), and standard turbine-spacing technical-potential models consistent
with those used in NREL assessments. The wide range reflects the genuine
variation in installed density across wind farms with different turbine
generations, spacing conventions, and terrain types. The baseline of
8.9 MW/km² represents a central estimate consistent with modern turbine
configurations at typical rotor-diameter spacing ratios. The same range is
applied to both regions since turbine-spacing physics are not
region-specific.

Citations: Ong et al. (2013); Bolinger and Bolinger (2022); Van Zalk and
Behrens (2018); Archer et al. (2019). `METHODOLOGY.md` §2.4 states the
same two baseline/range values (43–60 MW/km² solar, 4.1–13.7 MW/km² wind)
with the same citation set.

Sensitivity: `±` explored via `min`/`max` (not a symmetric `±20%` — these
are literature-reported bounds, not an arbitrary percentage band).
`config.scenario.active = min|max` resolves every region+technology pair
at that band edge in the same run (see
`09_methodology_assumptions.md`'s "Scenario selector" section — the
mechanism is shared between capacity factor and capacity density).

**Note the baseline shift**: METHODOLOGY.md §2.4 previously stated
30 MW/km² (solar) and 8 MW/km² (wind) as fixed values. The new baselines
(51.5 / 8.9) supersede those numbers — wind barely moves, but solar's
baseline installable capacity per km² rises by ~72%. This is an
intentional methodology update to NREL-sourced figures, not a bug; any
`installable_capacity_mw`/`annual_h2_production_t` computed before this
migration is stale and should be re-generated, not reconciled against.
