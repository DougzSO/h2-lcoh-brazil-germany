# 05 — Economic Model

## Technical hydrogen potential (`src/potential/`)

`potential/h2_potential.py`'s `calculate_h2_potential()` /
`run_all_potential()` — installable capacity, annual electricity yield, and
annual H₂ output per candidate site — is documented in
[04_spatial_methodology.md](04_spatial_methodology.md) alongside
`site_selection.py`, since it reads that module's
`candidate_sites_{region}_{tech}.geojson` output directly and its formulas
are pure siting/technical-potential arithmetic (METHODOLOGY.md §2.4), not
an LCOH/WACC computation. It is the bridge module between the spatial
pipeline and this file's LCOH model: a future per-site LCOH pass would
consume `h2_potential.py`'s per-site `installable_capacity_mw` /
`annual_h2_production_kg` output without requiring any change to
`calculate_lcoh()`'s pure-function signature below.

## Economic one-at-a-time sensitivity sweep (`src/sensitivity/`)

`sensitivity/sensitivity_analysis.py`'s `run_sensitivity(config) ->
pd.DataFrame` (METHODOLOGY.md §2.7) is a one-at-a-time sweep, for each
region x renewable technology, over: WACC (THIS technology's own
`wacc.<solar|wind>` `{min, max}` `RangeParam` bounds -- technology-specific
since the WACC schema refactor, `_wacc_sweep_values(region_cfg,
renewable_tech, steps)`; previously read a single shared `wacc.range`
tuple identical for solar and wind, a latent bug fixed alongside the
schema change, see ADR-009), electrolyzer CAPEX (+/-20% of the PEM base value),
electrolyzer efficiency (+/-20% of the PEM base value, recomputing
`hydrogen_output_kg_per_kw_year` alongside it so the sweep doesn't only
affect one side of the LCOH ratio), electrolyzer technology (PEM base vs.
alkaline, a discrete swap rather than a continuous sweep), and the
region's CAPEX multiplier (`config.sensitivity.capex_multiplier_range`).
Every step calls `lcoh_model.calculate_lcoh()` directly — no LCOH math is
duplicated here. Written to `outputs/tables/economic_sensitivity.csv`.
`run_all_sensitivities(config) -> Dict[str, Any]` runs this alongside the
MCDA weight-perturbation-vs-VIKOR concordance check documented in
[04_spatial_methodology.md](04_spatial_methodology.md#topsis-and-vikor-multicriteria-formulations)
for both regions and both siting technologies.

## Combined min/baseline/max LCOH range (`decomposition.run_scenario_range()`)

`run_scenario_range(region, config, renewable_tech) -> dict` sweeps all 3
COMBINED `scenario_params.yaml` branches (`min`, `baseline`, `max`) for one
region+tech pair in a single call, via `_lcoh_for()`'s `scenario` parameter
(default `"baseline"`). There is no `config.scenario.active` any more --
the pipeline always executes all three combined scenarios for every region
and technology, never a single globally-selected branch (ADR-009 below).

**WACC, capacity_factor/full_load_hours, and renewable CAPEX all move
together at each branch** -- this is the "combined scenario" refactor: WACC
was migrated from a scalar + shared `range` tuple to a per-technology
`{min, baseline, max}` `RangeParam` (`RegionWaccConfig`), and renewable
CAPEX gained a scenario resolver
(`_effective_renewable_capex_for_scenario()`) reading
`renewables.<tech>.capex_range` (or, for Brazil onshore wind specifically,
`regions.brazil.onshore_wind_capex_range_override_usd_per_kw`, see
"Region-specific renewable CAPEX override" below). Combined "min" (worst
case) pulls WACC to its own RangeParam `max` (highest financing cost --
see `config_loader.resolve_inverted_param()`, the one place this
inversion happens), capacity_factor/capacity_density to their own `min`,
and renewable CAPEX to its range's `max` (highest capital cost), all
simultaneously; combined "max" (best case) is the mirror image. This
replaced an earlier, narrower design where only capacity_factor varied
across scenario branches (WACC/CAPEX/OPEX were fixed scalars) -- see
ADR-009. The true LCOH range is the min/max of the 3 *computed* LCOH
values, never assumed monotonic in scenario branch.

`run_all_decompositions()` calls this for both regions × both techs and
appends 3 `scenario_range` rows + 1 `range_summary` row per pair to
`decomposition.csv` (columns: `scenario`, `wacc_used`,
`capex_multiplier_used`, `renewable_capex_used`, `lcoh_usd_per_kg` for the
per-scenario rows; `lcoh_min`, `lcoh_baseline`, `lcoh_max`,
`lcoh_range_str`, `range_width_pct` for the summary row) alongside the
pre-existing `actual`/`wacc_swap` rows.

## Sobol global sensitivity analysis (`src/sensitivity/`)

`sensitivity/sensitivity_analysis.py`'s `run_sobol_analysis(config) ->
Dict[str, pd.DataFrame]` complements the OAT sweep above with a
variance-based global sensitivity analysis (Saltelli sampling + Sobol
estimator, via SALib), for the same region x renewable_tech pairs. Where
OAT varies exactly one parameter at a time from a fixed base case, Sobol
decomposes LCOH's output variance into each parameter's first-order effect
(S1, its influence alone) and total effect (ST, its influence alone plus
every interaction it participates in) — `ST - S1 > 0` is a real
interaction OAT cannot detect by construction. Every sample is evaluated
through the unmodified `calculate_lcoh()`; no LCOH math is duplicated.
Written to `outputs/tables/sobol_{region}_{renewable_tech}.csv` (columns:
`parameter, S1, S1_conf, ST, ST_conf, interaction`).

**Parameter scope, and why it is 3 parameters, not 5:** the task that
added this feature specified 5 candidates (CAPEX, OPEX, WACC,
capacity_factor, capacity_density), gated by "only include a parameter if
it already has a literature min/max range in the config; if it's still a
scalar, that's disqualifying." Checked directly against
`config_loader.py`'s schema:

| Candidate | Range in `scenario_params.yaml`? | Included? |
|---|---|---|
| WACC | `RegionConfig.wacc.<solar\|wind>` (`RangeParam`, technology-specific since the WACC schema refactor) | Yes |
| capacity_factor | `TechRegionParams.capacity_factor` (`RangeParam`) | Yes |
| Renewable CAPEX | `RenewableTechConfig.capex_range` — this is the CAPEX that actually reaches `calculate_lcoh()`, via `calculate_lcoe()` → the required `lcoe_usd_per_kwh` argument | Yes (named `renewable_capex_usd_per_kw` in the CSV) |
| Electrolyzer CAPEX/OPEX | `ElectrolyzerTechConfig.capex_usd_per_kw` / `ElectrolyzerSharedConfig.opex_pct_of_capex` are still bare floats — no range anywhere | No — excluded rather than assigned an invented ±% band |
| capacity_density_mw_per_km2 | Has a range, but is structurally irrelevant here: `calculate_lcoh()`'s signature has no capacity/area/density parameter at all (LCOH is a per-kW cost ratio; density only changes `h2_potential.py`'s installed-capacity output, a separate quantity — see [04_spatial_methodology.md](04_spatial_methodology.md)) | No — a "CAPEX × density" interaction on LCOH is undefined, not merely small |

Also see ADR-006 in [06_technical_decisions_log.md](06_technical_decisions_log.md).
`lcoe_usd_per_kwh` is recomputed per Sobol draw from that draw's own
wacc/capacity_factor/renewable_capex (via `calculate_lcoe()`), unlike the
OAT sweep above which holds it fixed at baseline while sweeping wacc or
capacity_factor individually — necessary so Sobol's variance decomposition
doesn't understate wacc's/capacity_factor's true influence, since both
genuinely drive the renewable LCOE embedded in every LCOH figure.

**Verified live after the WACC-schema/combined-scenario refactor**
(`n_samples=1024` per region/tech pair, 5,120 evaluations each, current
per-technology WACC bounds -- BR solar [8%,20%], BR wind [10%,24%], DE
solar [2%,5%], DE wind [2%,7%]): `sum(S1)` in [0.990, 0.996] for all 4
pairs (consistent with a near-additive model); WACC has the highest S1 for
3 of 4 pairs -- Brazil solar (S1=0.641), Brazil wind (S1=0.719), Germany
wind (S1=0.552) -- but capacity_factor is highest for Germany solar
(S1=0.609, vs. WACC's 0.194), since Germany solar's WACC range [2%,5%] is
narrow relative to its capacity_factor range [10%,15%]; this is the same
qualitative pattern (which parameter dominates for which pair) as before
the WACC-schema refactor, with S1 values shifted by the now-technology-
specific WACC bounds (previously solar and wind shared one WACC range per
region, a latent bug -- see ADR-009 and `_sobol_bounds()`'s own FIXED BUG
note). `ST - S1` for the dominant parameter in each pair is very close to
zero -- expected finite-sample Sobol estimation noise when the true
interaction is near zero, not a violation of the theoretical ST ≥ S1
property. `run_all_sensitivities(config)` runs this alongside
`run_sensitivity()` and the MCDA check; `run_pipeline.py`'s
`stage_sensitivity()` reports it as always-succeeding, pure config-driven
math (same category as the OAT sweep).

### Literature basis for the Sobol analysis design

The global variance-based sensitivity analysis using Sobol indices
(Saltelli et al., 2008, 2019) is conducted on the three parameters with
the largest documented uncertainty ranges in the LCOH model: WACC
(region- and technology-specific range), capacity factor (region- and
technology-specific range), and renewable generation CAPEX
(technology-specific range) — the same three parameters selected in the
table above, now with their sampling design stated explicitly. Each
parameter range is drawn directly from the literature-validated bounds
stored in `scenario_params.yaml` (WACC: `regions.<region>.wacc.<solar|wind>`
min/max, technology-specific; capacity factor:
`technologies.<tech>.<region>.capacity_factor` min/max; renewable CAPEX:
`renewables.<tech>.capex_range`). A total of 5,120 model
evaluations are used per region/technology combination, generated via
Saltelli sampling with N=1,024 base samples for k=3 parameters
(N × (2k+2) = 5,120), providing sufficient convergence for first-order
(S1) and total-order (ST) indices in a near-additive model (confirmed by
Σ S1 ≈ 0.99 across all four region/tech pairs, matching the "Verified
live" figure above). Electrolyzer CAPEX/OPEX are excluded from the Sobol
analysis because no literature-validated range exists for these
parameters in `scenario_params.yaml` (they are stored as single scalars);
capacity density is excluded because `calculate_lcoh()` has no capacity or
area parameter — density enters only the technical-potential calculation,
not the LCOH formula itself. Citation: Saltelli et al. (2008, 2019).
`METHODOLOGY.md` §2.7 states the same sampling design (N=1,024, k=3,
5,120 evaluations) and the same exclusion rationale, with the same
citation.

## Discounted LCOH lifetime formula

Implemented as a pure function, `calculate_lcoh()` in
`src/economics/lcoh_model.py`, which has **no project imports** — standard
library only, by design, so it can be unit-tested and reasoned about in
complete isolation from configuration or region logic.

```
LCOH = ( CAPEX_total
         + Σ_t [ OPEX_t / (1+WACC)^t ]
         + Σ_t [ StackReplacement_t / (1+WACC)^t ]
         + Σ_t [ WaterCost_t / (1+WACC)^t ]
         + Σ_t [ EnergyCost_t / (1+WACC)^t ]
         − Σ_t [ Incentive_t / (1+WACC)^t ]
       )
       / Σ_t [ H2Production_t / (1+WACC)^t ]
```

Sums run over `t = 1 .. lifetime_years` (end-of-year discounting); year-0
CAPEX is undiscounted. Electricity cost is a **required** argument
(`lcoe_usd_per_kwh`, no default) — annual electrolyzer electricity
consumption is `full_load_hours` kWh per kW of capacity, valued at the
renewable LCOE. This is deliberate: making electricity cost required closes
a gap that previously allowed LCOH to go negative whenever a production
incentive exceeded electrolyzer-only costs (see the module docstring).

`calculate_lcoe()`, in the same module, computes the discounted LCOE for
the renewable generation technology feeding the electrolyzer — CAPEX and
OPEX discounted by that technology's own WACC, divided by discounted annual
energy (`capacity_factor × 8760` kWh/kW/yr). This LCOE is what feeds
`calculate_lcoh()`'s `lcoe_usd_per_kwh` argument; the two functions are
chained in `economics/decomposition.py: _renewable_lcoe()` /
`_lcoh_for()`.

Stack replacement is scheduled at integer multiples of
`stack_lifetime_hours / full_load_hours` (converting operating-hour stack
life into a calendar interval via the technology's full-load hours),
excluding a replacement that would fall exactly in the plant's final year.

## Electrolyzer specs

From `config/scenario_params.yaml: electrolyzer`:

| Parameter | PEM (baseline) | Alkaline (sensitivity) |
|---|---|---|
| CAPEX | 1,200 USD/kW | 700 USD/kW |
| Specific energy consumption | 52 kWh/kg H₂ | 51 kWh/kg H₂ |
| Stack replacement | 15% of CAPEX | 10% of CAPEX |
| Stack lifetime | 80,000 h | 90,000 h |

Shared across both technologies: OPEX 2% of CAPEX/yr, 20-year lifetime,
water cost 5.5556 USD/m³ × 9.0 L/kg H₂ (= 0.05 USD/kg H₂ exactly, per
`METHODOLOGY.md` §2.5).

PEM is the baseline technology, reflecting superior load-following
characteristics relative to alkaline. **PEM is the only technology used in
`economics/decomposition.py`'s actual / WACC-swap / inversion-point runs**
(`_electrolyzer_defaults(config, technology="pem")` default); alkaline is
a sensitivity-only variant, exercised exclusively by
`sensitivity/sensitivity_analysis.py`, never by decomposition.

> ⚠️ Point to validate: `water_consumption_l_per_kg = 9.0 L/kg` is a
> plausible technical value not stated explicitly in `METHODOLOGY.md` (the
> methodology only states the aggregate 0.05 USD/kg figure). If this
> specific consumption value changes, `water_cost_usd_per_m3` must be
> recalibrated to `0.05 / (water_consumption_l_per_kg / 1000)` to preserve
   the methodology's stated aggregate — see the inline comment in
   `scenario_params.yaml`.

### Literature basis for electrolyzer parameters

**PEM CAPEX:** 1,200 USD/kW, within the 700–1,400 USD/kW range reported by
IRENA (2020) and adjusted upward from IRENA's central estimate to reflect
post-2021 supply-chain cost inflation documented by IEA (2023). The World
Bank/ESMAP (2026) technical report on electrolyzer characteristics
corroborates this as a defensible 2023–2025 baseline for commercial-scale
PEM systems.

**PEM specific energy consumption:** 52 kWh/kg H₂, the consensus baseline
for commercial PEM systems in 2023–2025 per IRENA (2020) and consistent
with the 52.6 kWh/kg value reported by Glenk and Reichelstein (2019).
Equivalent to approximately 64% efficiency on a lower heating value basis,
consistent with IRENA's "average conditions" benchmark.

**PEM stack replacement:** 15% of CAPEX at 80,000 operating hours,
following IRENA (2020). Some sources (ESMAP, 2026) report the stack as
representing up to 25% of total system CAPEX; the 15% replacement-cost
figure used here is interpreted as the service cost of stack replacement
(labour, ancillaries, and stack hardware net of reuse credits) rather than
the stack's full share of system cost — a distinction made explicit here
because the two figures are not directly comparable.

**PEM fixed OPEX:** 2% of CAPEX per year, consistent with IRENA (2020) and
corroborated by the absolute cost figure of approximately 23–24 USD/kW/yr
implied by German project-level data reviewed for this study.

**Electrolyzer lifetime and water cost:** project lifetime of 20 years
follows the standard IRENA (2020) assumption for electrolyzer-based
hydrogen systems. Water cost of 0.05 USD/kg H₂ is computed as
`water_cost_usd_per_m3` (5.5556 USD/m³) × `water_consumption_l_per_kg`
(9.0 L/kg H₂) / 1,000 L/m³, consistent with `METHODOLOGY.md`'s stated
aggregate water-cost figure; the unit water price itself is a placeholder
pending local (region-specific) verification — see the ⚠️ flag above this
section, which this citation does not resolve.

**Alkaline CAPEX:** 700 USD/kW, consistent with IRENA (2020)'s range for
commercial alkaline systems above 10 MW and corroborated by IEA (2022).
**Alkaline specific energy consumption:** 51 kWh/kg H₂, reflecting mature
commercial alkaline technology (IRENA, 2020), and conservatively set
slightly above 2025 performance targets of approximately 49 kWh/kg.
**Alkaline stack replacement:** 10% of CAPEX at 90,000 operating hours.
Sources including ESMAP (2026) report the alkaline stack as representing
up to 45% of total system CAPEX; as with PEM above, the 10% figure here is
the periodic service-replacement cost, not the full stack share of system
CAPEX — this distinction should be footnoted wherever this parameter is
cited in the manuscript.

Citations: IRENA (2020); IEA (2022, 2023); Glenk and Reichelstein (2019);
World Bank/ESMAP (2026). `METHODOLOGY.md` §2.5 states the PEM baseline
values (CAPEX, specific energy consumption, OPEX, stack replacement) with
the same IRENA (2020)/IEA (2023)/Glenk and Reichelstein (2019) citation
set; the alkaline sensitivity values and the stack-share-vs-replacement-
cost distinction are documented here and are not restated in
`METHODOLOGY.md`, which covers alkaline only briefly (§2.5, "Alkaline
electrolysis was retained as an explicit sensitivity scenario...").

## CAPEX multiplier (1.15 BR vs. 1.00 DE)

A country-specific CAPEX multiplier applies to electrolyzer CAPEX only:
1.15 for Brazil (import duties and freight differentials relative to the
European domestic supply chain), 1.00 for Germany. Enforced in
`_lcoh_for()`: **the CAPEX multiplier always resolves from the target
region's own config and never travels with a swapped WACC** — this is a
named critical invariant in `decomposition.py: decompose_wacc_swap()`'s
docstring, since the whole point of the WACC-swap counterfactual is to
isolate the financing-cost effect from the CAPEX-side effect.

### Literature basis for the CAPEX multiplier

Brazil's country-specific CAPEX multiplier of 1.15 reflects import duties
on electrolyzer equipment, freight differentials relative to the European
domestic supply chain, and local content requirements documented in
Brazilian energy sector procurement. Germany receives a multiplier of 1.00
as the reference market. The 1.15 value is consistent with the
cost-of-system differentials implied by IRENA (2024)'s regional LCOE
spread between Brazil and comparable European markets, and with the
cost-of-balance-of-system analysis for Brazilian solar projects reviewed
in the literature.

**Scope correction relative to the original drafting note:** the
justification draft for this parameter (change_list.md, esboço F)
described the multiplier as "applied uniformly to all capital expenditure
components (renewable generation and electrolyzer) in the Brazil model
runs." This does not match the verified implementation and is corrected
here per the source-of-truth rule (root `CLAUDE.md`): `calculate_lcoh()`
(`src/economics/lcoh_model.py:96`) is the only function that consumes
`capex_multiplier` (`capex_total = capex_usd_per_kw * capex_multiplier`),
and `calculate_lcoh()` is called exclusively with electrolyzer CAPEX
(`_lcoh_for()` in `decomposition.py`). `calculate_lcoe()`
(`src/economics/lcoh_model.py:165`) — which computes the renewable
generation LCOE fed into `calculate_lcoh()`'s `lcoe_usd_per_kwh` argument
— has **no `capex_multiplier` parameter in its signature at all**, and
`_renewable_lcoe()` (both in `decomposition.py` and its mirror in
`sensitivity_analysis.py`) never passes one. The multiplier therefore
applies to electrolyzer CAPEX only, never to solar/wind CAPEX, in the
current implementation. `METHODOLOGY.md` §2.5 states the same 1.15/1.00
values with the same IRENA (2024) citation and the same corrected scope.

## Pure zero-incentive baseline rule

All decomposition runs (`decompose_actual`, `decompose_wacc_swap`,
`run_scenario_range`, and `competitiveness_frontier.py`'s frontier grid)
route through the private helper `_lcoh_for()`, which **always** passes
`incentive_value_usd_per_kg=0.0,
incentive_duration_years=0.0` to `calculate_lcoh()` — it deliberately never
reads `region_cfg.incentives`, even though that data exists on the config
object. This is enforced as ADR-002 (see
[06_technical_decisions_log.md](06_technical_decisions_log.md)) and is the
fix for a previously identified bug where Germany's incentive silently
contaminated the baseline comparison. Applying real incentive values is
exclusively the job of `economics/incentive_scenarios.py`, which calls
`calculate_lcoh()` directly with its own incentive value/duration.

## WACC-swap counterfactual

WACC is **technology-specific, not a regional average** — solar and wind
projects in the same region carry different financing costs
(`_tech_wacc()`: `region_cfg.wacc.solar`/`.wind`, each a `{min, baseline,
max}` `RangeParam` since the WACC schema refactor -- ADR-009). Baseline
values and each technology's OWN literature range (no longer a single
range shared between solar and wind within a region):

| Region | Technology | Baseline WACC | {min, max} range |
|---|---|---|---|
| NE Brazil | Solar | 11.0% | [8%, 20%] |
| NE Brazil | Wind | 12.0% | [10%, 24%] |
| North Germany | Solar | 3.0% | [2%, 5%] |
| North Germany | Wind | 4.0% | [2%, 7%] |

**Brazil's baselines were raised from 8.0%/9.0%** to reflect
emerging-market financing costs, while **keeping solar below wind** —
consistent with Steffen (2020), the source this section's WACC-swap
rationale already cites for the solar<wind financing-cost spread used in
*both* regions. A version of this change that additionally reversed the
ordering to solar>wind for Brazil specifically ("higher perceived
technology risk") was considered and rejected: it contradicted the cited
literature basis with no new citation offered, and its own motivating
claim — an "inverted" wind/solar LCOH relationship needing correction —
was empirically false (Brazil wind was already cheaper than solar under
the prior 8%/9% baseline: 5.61 vs. 6.09 USD/kg). WACC was subsequently
migrated from a scalar-plus-shared-`range` schema (one `[min, max]` tuple
covering both solar and wind in a region) to a per-technology `RangeParam`
(ADR-009), so solar and wind now carry independently literature-sourced
ranges rather than one shared band — Brazil wind's own upper bound (24%)
in particular reflects the earlier extended-search ceiling that used to be
a one-off widening of the shared range solely for the wind inversion
search; it is now simply wind's own permanent range.

### Literature basis for WACC values

Brazil's baseline WACC (`regions.brazil.wacc`: 11.0% solar, 12.0% wind)
reflects the financing conditions documented for renewable energy projects
in emerging markets with Brazil's sovereign risk profile. These values are
consistent with the 10–13% nominal WACC range reported for Brazilian
renewable assets in IRENA (2023, 2024), Egli, Steffen and Schmidt (2018),
and Steffen (2020), and with the country-risk premium decomposition applied
to hydrogen export cost modelling by Terrapon-Pfaff et al. (2025) for
comparable emerging markets. The solar WACC is held one percentage point
below the wind WACC, consistent with the systematic solar-below-wind
ordering documented across mature and emerging markets by Steffen (2020)
and confirmed for the Brazilian context by IRENA (2024) — the same
ordering already relied on above for both regions. Brazil solar's range
`[0.08, 0.20]` spans the lower bound of recent auction-implied financing
costs to a conservative upper bound for emerging-market risk scenarios;
Brazil wind's range `[0.10, 0.24]` extends further, reflecting both
unblended project-finance conditions for wind specifically and (at its
upper bound) the extreme emerging-market risk ceiling originally tested by
this study's wind-specific inversion-point search (now the competitiveness
frontier, section 2.6, METHODOLOGY.md).

Germany's baseline WACC (`regions.germany.wacc`: 3.0% solar, 4.0% wind) is
consistent with the 2.7–3.1% solar and 3.0–4.0% wind ranges reported by
Steffen (2020) for German utility-scale assets under pre-2022 stable
macroeconomic conditions, and broadly corroborated by IRENA (2023, 2024)
for Western European markets. Solar's range `[0.02, 0.05]` and wind's
range `[0.02, 0.07]` both capture the low-rate environment of 2018–2021
and the post-2022 interest-rate environment, wind's wider upper bound
reflecting its documented higher ceiling in the same Steffen (2020) range.

The spread between Brazil and Germany (~8 percentage points at baseline)
represents a financing-cost differential consistent with the
emerging-market premium documented in Egli, Steffen and Schmidt (2018) and
Brändle, Schönfisch and Schulte (2021), and constitutes the central
tension this study's decomposition and inversion-point analysis is
designed to quantify. Citations: IRENA (2023, 2024); Egli, Steffen and
Schmidt (2018); Steffen (2020); Terrapon-Pfaff et al. (2025); Brändle,
Schönfisch and Schulte (2021). `METHODOLOGY.md` §2.5 carries the same
values and the same citation set — see the corresponding paragraph there.

## Renewable CAPEX and OPEX — literature basis

**Since ADR-012, `renewables.solar_pv`/`renewables.onshore_wind` are no
longer "global" baselines shared unchanged by both regions — they are
Germany's values (Abuzayed & Hartmann, 2022), and Brazil is shielded from
them via its own region-specific overrides** (see "Region-specific
renewable CAPEX override" below), so this section now describes two
genuinely different country figures per technology, not one shared figure.

**Solar PV CAPEX — Germany (shared `renewables.solar_pv.capex_usd_per_kw`):**
660 USD/kW, range 500–900 USD/kW, converted from Abuzayed and Hartmann
(2022)'s reported 600 EUR/kW at a fixed EUR/USD rate of **1.10**. Brazil's
own solar CAPEX is unaffected — see the override section below.

**Onshore wind CAPEX — Germany (shared `renewables.onshore_wind.
capex_usd_per_kw`):** 1,317 USD/kW, range 1,000–1,700 USD/kW, converted
from Abuzayed and Hartmann (2022)'s reported 1,197 EUR/kW at the same
fixed 1.10 EUR/USD rate. Brazil's own onshore wind CAPEX is unaffected —
see the override section below.

**Prior global baseline, now Brazil-only (via override, unchanged
figures):** Solar 700 USD/kW (range 500–1,000 USD/kW), consistent with the
global weighted-average utility-scale solar PV cost of 758 USD/kW reported
by IRENA (2024) for 2023. Onshore wind 1,050 USD/kW (range 800–1,550
USD/kW), reflecting IRENA (2024)'s reported Brazilian onshore wind cost of
approximately 1,099 USD/kW for 2023 and EPE auction-clearing benchmarks —
this was already a Brazil-specific override before ADR-012 (see below);
ADR-012 only changed what the *shared* value defaults to when no override
is set, which now affects Germany, not Brazil.

**OPEX:** solar PV fixed OPEX of 1.5% of CAPEX/yr
(`renewables.solar_pv.opex_pct_of_capex`) and onshore wind fixed OPEX of
2.0% of CAPEX/yr (`renewables.onshore_wind.opex_pct_of_capex`) are within
the 1.0–1.5% (solar) and 2.0–3.0% (wind) ranges documented by IRENA (2023,
2024) and EWI (2020). Project lifetime of 25 years for both technologies
(`renewables.<tech>.lifetime_years`) is the standard assumption in IRENA
long-term cost modelling and is consistent with the technical lifetimes of
modern utility-scale assets.

`METHODOLOGY.md` §2.5 states the same CAPEX/OPEX/lifetime values with the
same citation set (IRENA 2023, 2024; IEA 2023; EWI 2020).

## Region-specific renewable CAPEX override

`RenewablesConfig` (solar_pv/onshore_wind CAPEX, OPEX%, lifetime) has **no
region axis** — a single `renewables.<tech>.capex_usd_per_kw` value is
shared by both Brazil and Germany by default. `RegionConfig` has four
optional override fields: `onshore_wind_capex_override_usd_per_kw` /
`onshore_wind_capex_range_override_usd_per_kw` (Brazil only, unchanged
since ADR-009) and, since **ADR-012**,
`solar_pv_capex_override_usd_per_kw` / `solar_pv_capex_range_override_usd_per_kw`
(Brazil only). `None` (the default) means "use the shared value" — Germany
sets none of the four, so it is unaffected by construction, not by
convention.

Brazil's onshore wind: 1,050 USD/kW (unchanged; the shared baseline
Germany now uses instead is 1,317 USD/kW, see above). Brazil's solar PV:
700 USD/kW (unchanged; the shared baseline Germany now uses instead is 660
USD/kW). **The solar override exists specifically because ADR-012 changed
what the shared `renewables.solar_pv` value defaults to** — before
ADR-012, Brazil solar had no override at all because it didn't need one
(Brazil and Germany's solar CAPEX were numerically identical). The
override's job is not to make Brazil solar cheaper or more expensive; it
is to hold Brazil's solar CAPEX at its own pre-ADR-012 value while the
shared default it used to inherit implicitly now points at Germany's
figure instead.

**Literature basis:** Brazil's 1,050 USD/kW onshore wind override reflects
IRENA (2024)'s reported Brazilian onshore wind cost of approximately 1,099
USD/kW for 2023 — one of the lowest markets globally — and is consistent
with EPE auction-clearing cost benchmarks for Northeast Brazil projects.
Brazil's 700 USD/kW solar override reflects the global weighted-average
utility-scale solar PV cost of 758 USD/kW reported by IRENA (2024) for
2023. `METHODOLOGY.md` §2.5 states the same override values with the same
citations.

`decomposition._effective_renewable_capex(region_cfg, renewable_cfg,
renewable_tech)` is the single resolution point, generalized in ADR-012
from an `onshore_wind`-only `if` branch to a `_CAPEX_OVERRIDE_FIELD =
{"solar_pv": ..., "onshore_wind": ...}` lookup (same pattern
`_effective_renewable_capex_for_scenario()`'s
`_CAPEX_RANGE_OVERRIDE_FIELD` uses for the min/max branches) — reused by
`_renewable_lcoe()`, `run_lcoh_monte_carlo()`'s CAPEX sampling center, and
`sensitivity_analysis.py`'s own `_renewable_lcoe()` — all three would
otherwise have silently diverged onto different Brazil CAPEX values (the
same class of cross-module drift bug ADR-history in this project has hit
before).

> ⚠️ Point to validate: `sensitivity_analysis.py`'s `_sobol_bounds()`
> reads `renewable_cfg.capex_range` directly (the shared
> `renewables.<tech>.capex_range`), not through
> `_effective_renewable_capex_for_scenario()` — so it has never respected
> either region's range override. This was already true for Brazil
> onshore wind before ADR-012 (a pre-existing, undocumented-until-now gap:
> Sobol's wind CAPEX bound was `[1000, 1800]`, not Brazil's actual
> `[800, 1550]` override range) and now applies to Brazil solar too, since
> Brazil solar's effective CAPEX only diverged from the shared value
> starting with ADR-012. Not fixed here — out of scope for ADR-012's own
> change set (economics decomposition and the YAML, not
> `sensitivity_analysis.py`) — flagged for a future session before citing
> a Brazil-specific Sobol CAPEX sensitivity result.

**Verified live (post-ADR-012)**: Brazil solar 7.335 USD/kg (unchanged),
Brazil wind 6.105 USD/kg (unchanged, wind still cheaper) — Germany solar
7.592 USD/kg (down from 7.736, Germany's CAPEX dropped 700→660) and
Germany wind 4.186 USD/kg (up from 4.159, Germany's CAPEX rose 1,300→1,317).

**Combined-scenario range sibling (ADR-009):** the single-value override
above covers only the BASELINE branch. For the combined min/max scenarios'
own renewable-CAPEX resolution (`_effective_renewable_capex_for_scenario()`),
Brazil onshore wind additionally has
`onshore_wind_capex_range_override_usd_per_kw: [800.0, 1550.0]` in
`scenario_params.yaml` — a Brazil-specific range centered on its own 1,050
USD/kW baseline override, deliberately NOT the raw shared
`renewables.onshore_wind.capex_range` ([1000, 1800]) that Germany and both
regions' solar continue to use for their own min/max branches. Rationale:
applying the global range's raw endpoints to Brazil wind would have erased
its CAPEX advantage in the best-case (max) scenario, since the global
range's own minimum (1000) sits above the Brazil-specific baseline (1050)
by less than the relative spread IRENA (2024) documents for mature,
low-cost markets like Brazil's. The chosen range applies the same
asymmetric relative spread the global range shows around its own baseline
(-23%/+38% around 1300) to Brazil's 1050 baseline instead
(-24%/+48% around 1050 → [800, 1550]), preserving Brazil wind's relative
cost advantage over Germany at every combined-scenario branch, not just
baseline.

`decompose_wacc_swap(region_a, region_b, config, renewable_tech)` computes
region_a's LCOH using region_b's WACC for the **same** technology (solar
swaps with solar, wind with wind), while region_a's own CAPEX multiplier
and every other parameter stay fixed — isolating the financing-cost effect
alone.

## 2D WACC competitiveness frontier (`src/economics/competitiveness_frontier.py`)

Replaces the 1D `find_inversion_point()`/`run_inversion_point_scenario_sweep()`/
`run_extended_wind_wacc_search()` functions that used to live in
`decomposition.py` (all three deleted -- see ADR-009 below). Those
functions varied only Brazil's WACC against Germany's fixed baseline;
`competitiveness_frontier.py` instead sweeps BOTH countries' WACC
simultaneously over a 2D grid, for each renewable technology.

`compute_frontier(config, renewable_tech) -> pd.DataFrame`: builds
`wacc_br x wacc_de` via `np.linspace(range.min, range.max,
config.competitiveness_frontier.wacc_steps)` (15-20 steps, YAML-configured)
per region, reading each region's own `wacc.<solar|wind>` `RangeParam`
bounds. At every grid point, calls `decomposition._lcoh_for()` for both
regions with `wacc_override` set to that grid point's WACC and
`scenario="baseline"` -- every other parameter (capacity factor, capacity
density, renewable CAPEX, OPEX, lifetime, electrolyzer) stays pinned at
baseline, reusing `_lcoh_for()`'s existing LCOH pathway verbatim, no
formula duplicated. Writes `outputs/tables/competitiveness_frontier_{tech}.csv`
(columns: `technology`, `wacc_br`, `wacc_de`, `lcoh_br`, `lcoh_de`,
`delta_lcoh`, `parity_flag`). `parity_flag` is `True` when
`abs(delta_lcoh) <= parity_tolerance_pct * mean(lcoh_br, lcoh_de)` --
`parity_tolerance_pct` (0.05, i.e. 5% relative) is
`config.competitiveness_frontier.parity_tolerance_pct`, never hardcoded.

`summarize_frontier(config, renewable_tech, frontier_df) -> dict` computes
two subsidy-equivalence metrics per technology:

1. **WACC-gap closure**: fixes Germany's WACC at its own baseline and
   root-finds (via `scipy.optimize.brentq`, the SAME mechanism the old 1D
   search used -- scoped only inside this function, not reintroduced as a
   standalone config concept) the Brazilian WACC at which
   `LCOH_BR == LCOH_DE(baseline)`, bounded by Brazil's own `wacc.<tech>`
   range. If no sign change exists (`gap_low * gap_high > 0`), reports "No
   frontier found ... Brazil always cheaper/more expensive" -- a valid
   result, not suppressed. When Brazil is already cheaper than Germany at
   baseline (a positive-headroom case, not a gap to close), the printed
   message switches from "WACC-gap closure" framing to a "WACC headroom"
   framing instead of misleadingly reporting a negative "closure" value --
   see `summarize_frontier()`'s own inline comment.
2. **Direct support**: `LCOH_BR(baseline) - LCOH_DE(baseline)` directly, no
   root-finding needed -- the flat per-kg production credit that would
   achieve parity today without any WACC change.

`run_all_frontiers(config)` runs both functions for both technologies;
called once from `run_pipeline.py`'s `stage_economics()`, after
`run_all_decompositions()`'s three combined scenarios have already
completed (not from `decomposition.py` itself, keeping that module's scope
to the decomposition experiments).

### Verified live (solar and onshore wind, current WACC/CAPEX baselines)

**Pre-ADR-012 figures** (for historical comparison — see 2D frontier
section's own earlier verification): solar parity at `wacc_br=11.92%`,
direct support -0.40 USD/kg; wind no frontier found, direct support +1.95
USD/kg.

**Post-ADR-012** (Germany's CAPEX/capacity-factor updates shift both
countries' baseline LCOH; the sign of every metric was left to emerge
naturally, not forced — see `competitiveness_frontier.py`'s own no-forced-
sign design, unchanged by ADR-012):

- **Solar:** parity grid point still exists; root-finding along the
  `wacc_de=baseline` (3.00%) slice finds `wacc_br=0.11592088690811896`
  (11.59%, down from 11.92% pre-ADR-012 — Germany's cheaper 660 USD/kW
  CAPEX narrows Brazil's headroom slightly). Brazil's baseline solar LCOH
  (7.335 USD/kg) remains BELOW Germany's baseline (7.592 USD/kg, down from
  7.74) at Brazil's 11.0% baseline WACC, so this is still reported as WACC
  *headroom* (0.59 pp cushion, down from 0.92 pp) rather than a gap to
  close; `gap_closure_value_usd_per_kg` is negative (-0.257), and the
  direct-support metric is likewise negative (-0.257 USD/kg — no subsidy
  needed, Brazil remains competitive, same sign as pre-ADR-012).
- **Onshore wind:** still no sign change across the entire tested WACC_BR
  range at `wacc_de=baseline` (4.00%) -- `no_frontier_direction="always
  more expensive"`, unchanged. Direct-support value: +1.92 USD/kg (down
  slightly from +1.95, since Germany's own wind LCOH rose from 4.159 to
  4.186 USD/kg as its CAPEX rose 1,300→1,317 USD/kW, narrowing the gap a
  little even though Brazil's own wind figures are untouched).

Both technologies' signs (solar: Brazil competitive, no subsidy needed;
wind: Brazil not competitive, real subsidy required) are unchanged by
ADR-012 — only the magnitudes shifted, by the amount Germany's own updated
parameters would be expected to shift them.

## REHIDRO/IPCEI scenario functions and the incentive equivalence analysis (ADR-013)

**Superseded (kept for history):** earlier drafts of this study modeled
Brazil's REHIDRO and Germany's IPCEI Hy2Use support as flat per-kilogram
production credits — 1.00 USD/kg over 10 years for Brazil, a 2.00–4.50
EUR/kg range (baseline 3.25 EUR/kg, converted at a 2024 average EUR/USD
rate of 1.0822 to ≈3.52 USD/kg) for Germany — used as illustrative
placeholders pending a confirmed per-kilogram figure from either
programme. **Both values are now permanently zeroed** in
`scenario_params.yaml` (`production_credit_usd_per_kg: 0.0` for both
regions), not because the real number is still unknown, but because
closer examination of the actual programme designs (below) found that
**neither region's real incentive landscape is a flat per-kilogram
production credit at all** — continuing to model either as one, even a
clearly labeled placeholder, risked misrepresenting how each region's real
support actually works.

**`run_rehidro_scenario(region, config)` / `run_ipcei_scenario(region,
config)`** (Brazil-only / Germany-only, `ValueError` otherwise) still
exist and still call `lcoh_model.calculate_lcoh()` with the configured
(zero) credit — their output is therefore bit-for-bit identical to the
zero-incentive baseline (`decomposition.decompose_actual`), which is the
honest result given no such credit exists today, not a bug or a
regression from the pre-zeroing placeholder values.

### `summarize_incentive_equivalence(config, renewable_tech)` (new, ADR-013)

Replaces the retired placeholder-credit framing with a transparent
"incentive equivalence" analysis: what a hypothetical flat per-kilogram
credit WOULD need to be to close the baseline LCOH gap, computed directly
from `competitiveness_frontier.direct_support_range()`'s already-computed
gap (never re-derived or invented separately — the two modules cannot
silently diverge onto two different numbers for the same technology).
Called once per renewable technology from `run_all_incentive_scenarios()`,
results appended under `result["incentive_equivalence"]` (not written to
`incentive_scenarios.csv`, to avoid changing that file's `viz/plotting.py`-
consumed schema).

Brazil-side message branches on the sign of the baseline gap — the
literal requested template ("To close a $Z.ZZ/kg LCOH gap, Brazil would
need a production credit equivalent to $Z.ZZ/kg — REHIDRO/PHBC do not
currently provide this form of support") is only printed when the gap is
positive (Brazil more expensive); when Brazil is already cheaper (solar,
today), an honest "already has a $X.XX/kg advantage, no credit needed"
message is printed instead, since the literal template would otherwise
misrepresent a negative gap as something to "close." Germany-side message
deliberately does **not** print a fabricated per-kg-equivalent dollar
figure for IPCEI + H2Global + CCfD's combined effect — see "Why Germany's
indirect-support figure is not quantified" below.

**Verified live**: onshore wind (Brazil more expensive) — "To close a
$1.92/kg LCOH gap, Brazil would need a production credit equivalent to
$1.92/kg — REHIDRO/PHBC do not currently provide this form of support."
Solar (Brazil already cheaper) — "Brazil already has a $0.26/kg LCOH
advantage over Germany for solar_pv at baseline; no production credit is
needed to reach parity, and REHIDRO/PHBC do not provide one regardless."

### The real incentive landscape

**Germany:** IPCEI Hy2Use has approved 62 projects across participating EU
member states, providing state-aid-cleared investment aid toward capital
expenditure — tied to individual project investment plans, not a running
per-kilogram output payment. H2Global is a EUR900 million German federal
import market-maker: a double-auction mechanism buying hydrogen/derivatives
on the world market and reselling to European offtakers at the price the
market will bear, absorbing the difference — de-risks offtake, is not a
domestic per-kilogram production subsidy. Carbon Contracts for Difference
(CCfD) guarantee industrial users a fixed effective carbon price, paying
the difference against the prevailing EU ETS price — a carbon-price hedge,
not a hydrogen production credit. None of the three publishes a per-kg-H2
cost-reduction equivalent.

**Brazil:** REHIDRO provides a five-year suspension of PIS/Pasep and Cofins
federal taxes on capital goods/services for green hydrogen production
(2025–2030) — a CAPEX-side tax relief mechanism, structurally closer to
Germany's IPCEI investment aid than to a production credit. The Programa
Brasil Hidrogênio Verde / Programa Hidrogênio de Baixo Carbono (PHBC) is a
larger federal programme (R$18.3 billion, 2028–2032) but is not yet
operational, and its eventual instrument design (whether a per-kg credit
or something else) was not confirmed at the time of this study.

Citations: IPCEI Hy2Use project-approval documentation; H2Global programme
documentation; German federal CCfD policy documentation; REHIDRO
programme documentation (Brazilian federal green hydrogen policy
framework); PHBC programme announcement documentation. `METHODOLOGY.md`
section 2.8.1 ("Incentive Landscape and Policy Implications") states the
same programme descriptions with the same framing.

### Why Germany's indirect-support figure is not quantified

The requested message template implies a numeric "$W.WW/kg equivalent"
for Germany's combined IPCEI + H2Global + CCfD indirect cost reduction.
This is deliberately **not computed or printed as a number** anywhere in
this codebase: none of the three programmes publishes a per-kilogram
figure, and this study's own modeled inputs (Germany's WACC, renewable
CAPEX, capacity factor) are not decomposed into a "policy-attributable"
share versus a "market-conditions" share — there is no principled way to
read a dollar-per-kg policy-support figure out of Germany's already-low
modeled WACC (2–7%) without fabricating an attribution this study has no
evidence for. Printing an invented number here would violate the
explicit "do not invent fake incentive values" constraint this analysis
(ADR-013) was built under; `summarize_incentive_equivalence()`'s Germany
message states this limitation directly instead of silently omitting the
German side or guessing a figure.

`run_all_incentive_scenarios(config)` runs both regions' baseline (via
`decomposition.decompose_actual`) plus their respective (zero-credit)
REHIDRO/IPCEI LCOH — identical to baseline by construction — plus
`summarize_incentive_equivalence()` for both renewable technologies.

> ⚠️ Point to validate, discovered while building `viz/plotting.py`:
> unlike `decomposition.run_all_decompositions()` (writes
> `outputs/tables/decomposition.csv`) and
> `sensitivity_analysis.run_sensitivity()` (writes
> `outputs/tables/economic_sensitivity.csv`),
> `run_all_incentive_scenarios()` returns its dict but never persists it to
> `outputs/tables/`. `viz/plotting.py`'s `plot_incentive_scenarios()` is
> fully implemented and verified against a synthetic results dict, but
> `generate_all_plots()` has no CSV to read and always skips it with a
> warning. If incentive-scenario figures are needed as part of a
> `run_pipeline.py`-orchestrated run (not just an ad hoc
> `run_all_incentive_scenarios()` call whose return value is passed
> directly to `plot_incentive_scenarios()`), add a `to_csv()` call to this
> function — `viz/plotting.py` was deliberately left without an
> `economics/` import to make this change, per this session's "do not
> touch analytical modules" constraint.
