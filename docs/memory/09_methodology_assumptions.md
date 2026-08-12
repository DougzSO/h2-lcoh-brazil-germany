# 09 — Capacity Factor Assumptions

This file documents the literature review and study-specific decisions
behind `config.technologies.<solar_pv|onshore_wind>.<brazil|germany>.
capacity_factor` in `scenario_params.yaml` — the operating capacity factor
each renewable technology uses in both the technical H2 potential
(`potential/h2_potential.py`) and the LCOH calculations
(`economics/decomposition.py`, `economics/incentive_scenarios.py`,
`sensitivity/sensitivity_analysis.py`).

**Do not confuse this with `thresholds.min_capacity_factor`** (0.15): that
is a proposed, currently-unused-by-`exclusion_mask.py` binary EXCLUSION
threshold (see `04_spatial_methodology.md`'s own flagged design question)
— a materially different concept. `sensitivity_analysis.py`'s
`_base_lcoh_kwargs()` previously read `thresholds.min_capacity_factor`
for its OAT-sweep base case by mistake, silently using 0.15 for every
region+technology pair instead of each one's own resolved operating value
— fixed in the same change that introduced this file.

## Migration summary

Every capacity-factor value in this pipeline used to be a single scalar
(`regions.<region>.<solar|wind>.capacity_factor_default`/`_range`, or the
shared `renewables.<tech>.capacity_factor_default` fallback when no
region-specific override was set). This migration replaces ALL of those
scalars with a single literature-validated `{min, baseline, max}` range
per region+technology pair under `technologies.<tech>.<region>.
capacity_factor`, resolved via `config.config_loader.resolve_param()` at
a combined-scenario branch (`min`/`baseline`/`max`, passed explicitly by
the caller -- there is no `config.scenario.active` global selector any
more; the pipeline always executes all three combined scenarios in
sequence, see ADR-009 in `06_technical_decisions_log.md`). There is no
fallback: every consumer must resolve a `RangeParam`, and `resolve_param()`
raises `TypeError` immediately if handed a bare scalar.

`full_load_hours` (`capacity_factor * 8760` h/yr) is stored as its own
`RangeParam` alongside `capacity_factor`, rather than left for every
consumer to recompute inline — `config_loader.py`'s `TechRegionParams`
validator cross-checks the two agree (within 1 hour, for YAML rounding)
at load time, so they can never silently drift apart.

## Literature Review Summary

### Onshore Wind
- **Germany/Texas:** 30–35%
- **Brazil (high-resource):** 36.5% (3,200 full load hours, ONS data)
- **South Africa:** up to 49%
- **Sources:** MERRA-2/SARAH reanalysis, national grid operators

### Solar PV
- **Northeast Brazil:** 20% baseline, up to 30% optimal
- **Chilean Atacama:** >32%
- **Northern Europe (Germany):** 9–13%
- **Sources:** Meteorological reanalysis, validated metered time series

## Study-Specific Decisions

| Region  | Tech  | Range      | Baseline | FLH (baseline) | Source |
|---------|-------|------------|----------|-----------------|--------|
| Brazil  | Wind  | 30–42%     | 36%      | 3,153.6 h/yr    | IRENA (2024); MDPI (2025), ADR-012 |
| Brazil  | Solar | 20–30%     | 24%      | 2,102 h/yr      | MERRA-2 vs EPE |
| Germany | Wind  | 19.6–36.5% | 32%      | 2,803.2 h/yr    | Abuzayed & Hartmann (2022), ADR-012 |
| Germany | Solar | 9.2–14.6%  | 12%      | 1,051.2 h/yr    | Abuzayed & Hartmann (2022), ADR-012 |

Baselines for all four rows are unchanged from the pre-ADR-012 values (only
the min/max range and its literature basis moved for Brazil wind and both
Germany rows) — see the per-row citations below.

### Literature basis and citations, by region and technology

**Brazil solar PV:** the baseline capacity factor of 24% (min 20%, max
30%, equivalent to 2,102 full-load hours at baseline) was derived from
long-term Global Horizontal Irradiance averages for the Northeast Brazil
region as documented in the Global Solar Atlas (World Bank/ESMAP) and
corroborated by Pereira and Lima (2008) for the same geographic area. The
baseline is deliberately conservative relative to the best-site irradiance
values in the region (which can support capacity factors approaching 30%)
to avoid overstating technical potential at the regional scale; it
represents a resource-weighted average across the full
suitability-filtered area rather than the performance of individually
optimal sites.

**Brazil onshore wind (range updated, ADR-012):** the baseline capacity
factor remains 36% (3,153.6 full-load hours at baseline, unchanged), but
the min/max range was revised from 30–40% to **30–42%**, replacing the
Global Wind Atlas/ONS-atlas-average range basis with direct operational
evidence: IRENA (2024) reports Brazil achieved the highest global
weighted-average capacity factor for newly commissioned onshore wind, 56%,
driven by modern 4–6 MW turbines at 120–150 m hub heights, and MDPI (2025)
reports operational capacity factors for the ten largest Brazilian wind
farms ranging from Chuí (RS, older turbines) 35% up to Chafariz (PB) 43%
(Assuruá, BA, 41%; Lagoa dos Ventos, PI, 39%; Alto do Sertão, BA, 41%; Rio
do Vento, RN, 37%). The 56% global-high figure is deliberately excluded
from the baseline as a newest-fleet outlier not yet generalizable to the
region's full installed base. The baseline of 36% is retained as a
conservative operational-farm-weighted average, and the max was raised
from 40% to 42% to give margin above Chafariz's 43% for newer 5–6 MW
turbines while the min stays at a conservative 30%, reflecting that this
study's candidate sites sit closer to 100 m hub heights than the 120–150 m
range behind IRENA's headline 56% figure. The previous 30–40% range was
inconsistent with both this operational evidence and IRENA's global
benchmark. See `docs/memory/06_technical_decisions_log.md` ADR-012 and
`docs/memory/05_economic_model.md`.

**Germany solar PV (range updated, ADR-012):** the baseline capacity
factor remains 12% (1,051.2 full-load hours at baseline, unchanged), but
the min/max range was revised from 10–15% to **9.2–14.6%**, replacing the
Pfenninger and Staffell (2016)/IRENA (2024)-derived range with Abuzayed
and Hartmann (2022)'s reported German solar capacity factor range. The
baseline's original literature basis (Pfenninger and Staffell, 2016;
corroborated by IRENA, 2024) is retained for the baseline value itself,
which did not change.

**Germany onshore wind (range updated, ADR-012):** the baseline capacity
factor remains 32% (2,803.2 full-load hours at baseline, unchanged), but
the min/max range was revised from 30–35% to **19.6–36.5%**, replacing the
IRENA (2024, 2025)/EWI (2020)-derived range with Abuzayed and Hartmann
(2022)'s reported German onshore wind capacity factor range — materially
wider at the low end, reflecting a broader operational spread than the
prior narrow IRENA-modern-installations band. The baseline's original
literature basis (IRENA 2024, 2025; EWI 2020) is retained for the baseline
value itself, which did not change.

Citations: Global Solar Atlas (World Bank/ESMAP); Pereira and Lima (2008);
Global Wind Atlas; IRENA (2024, 2025); Brändle, Schönfisch and Schulte
(2021); Pfenninger and Staffell (2016); EWI (2020); Abuzayed and Hartmann
(2022); MDPI (2025). `METHODOLOGY.md` §2.5 states the same updated
baseline/min/max values with the same citation set per region and
technology.

These values replace the pre-migration scalars: Brazil solar
(0.24 default / [0.20, 0.28] range), Brazil wind (0.52 default /
[0.45, 0.60] range — a placeholder well above ONS's own 36.5% high-resource
figure), Germany solar (0.12 default / [0.10, 0.15] range), Germany wind
(0.30 default / [0.25, 0.38] range), plus the shared
`renewables.<tech>.capacity_factor_default` fallback (0.22 solar / 0.35
wind) that applied whenever no region override was set.

**Material consequence, verified live**: Brazil's onshore-wind baseline
capacity factor drops from the old 52% placeholder to a literature-backed
36% (ONS). This is not a rounding change — it materially raises Brazil's
onshore-wind LCOH (fewer annual operating hours per unit capacity) and can
flip which region has the lower baseline wind LCOH relative to the
pre-migration numbers. Treat any pre-migration wind LCOH comparison
(`decomposition.csv` generated before this change) as superseded, not as a
discrepancy to reconcile.

## Combined scenarios (supersedes the old single scenario selector)

`scenario.active`, a single top-level YAML switch, no longer exists (see
ADR-009, `06_technical_decisions_log.md`). The pipeline instead always
executes three COMBINED scenarios in sequence for every region+technology
pair: `min` (worst case: WACC at each RangeParam's own `max`, capacity
factor/density at their own `min`, renewable CAPEX at its range's `max`,
all simultaneously), `baseline` (every parameter at its own baseline), and
`max` (best case: the mirror image of `min`). This re-runs the ENTIRE
pipeline's economics-dependent stages -- technical potential
(`h2_potential.py`), LCOH, decomposition -- at each combined branch,
propagating physical AND financial parameter uncertainty into a range of
outcomes rather than a single point estimate. It is not a per-parameter
override; there is no mechanism (by design) to resolve, say, Brazil solar
at `max` while Germany wind resolves at `min` in the same run -- all
parameters for all region/technology pairs move to the SAME combined
branch together. Spatial suitability (`topsis.py`/`vikor.py`/
`exclusion_mask.py`/`site_selection.py`) is scenario-invariant (consumes
only raw resource/slope/distance rasters and land-use/protected-area/
boundary layers, never capacity_factor/capacity_density/WACC) and runs
once regardless of scenario -- see `04_spatial_methodology.md`.
