# 01 — Project Overview

## Research question

The study asks to what extent superior renewable resource endowment can
compensate for higher cost of capital in green hydrogen production, and
under what conditions that compensation breaks down. Rather than ranking
countries or sites against each other directly, the design isolates two
effects the literature usually treats separately — physical resource
quality and cost of capital — and recombines them in a controlled
counterfactual framework to locate an **inversion point**: the
financing-cost threshold at which the ranking between a resource-rich,
capital-constrained region and a resource-modest, capital-abundant region
reverses.

## Regional pair selection: Northeast Brazil vs. North Germany

Northeast Brazil and North Germany (Schleswig-Holstein and Niedersachsen)
were selected because they represent a near-maximal contrast in resource
endowment combined with a near-inverse contrast in financing conditions:

- Global Horizontal Irradiance: ~1,800–2,200 kWh/m²/yr (NE Brazil) vs.
  ~950–1,100 kWh/m²/yr (North Germany).
- Weighted average cost of capital: markedly higher in Brazil than in
  Germany for equivalent renewable asset classes.

Because the two effects (resource quality, financing cost) point in
opposite directions, the pair is analytically efficient for a decomposition
design — this opposition is the necessary condition for an inversion point
to exist at all.

## Academic rationale

The central empirical claim of the study is the LCOH decomposition and
inversion-point result — not the spatial suitability ranking, which serves
only as a supporting characterization of where hydrogen hubs could
plausibly be located. The design deliberately avoids the pairwise expert
elicitation used in the Analytic Hierarchy Process (AHP) for site
suitability, a step associated in the siting literature with low
inter-study agreement and subjective, non-representative weighting; TOPSIS
criterion weights are instead drawn from the GIS-MCDA renewable-siting
literature. See [04_spatial_methodology.md](04_spatial_methodology.md) for
the full multi-criteria formulation.

## Empirical result: the inversion point exists for solar, not for wind

Under baseline resource and capital-expenditure assumptions, the search
described above finds an inversion point for solar — Brazil's solar LCOH
equals Germany's baseline solar LCOH (7.74 USD/kg) at a Brazilian solar
WACC of 11.92%, only marginally above Brazil's own baseline solar WACC
(11.0%) — but finds **no** inversion point for onshore wind anywhere in
the tested range, extended up to 24% Brazilian WACC (well beyond any
currently discussed real-world de-risking scenario): Brazil's wind LCOH
remains above Germany's wind baseline throughout, and the gap widens
monotonically as WACC rises rather than narrowing toward zero. This is
reported as a substantive, publishable finding — not a bug or a null
result — demonstrating that the resource-quality-compensates-for-
financing-cost mechanism this study investigates does not operate
uniformly across technologies. See
[05_economic_model.md](05_economic_model.md#brents-method-inversion-point-search)
for the full verified figures and `METHODOLOGY.md` §2.6.

## Four-stage pipeline structure

The codebase mirrors the four analytical stages of the methodology one to
one, with no abstraction layer that does not correspond to an actual
methodological step:

1. **Spatial suitability assessment** (`src/spatial/`) — establishes where
   hydrogen hubs could plausibly be sited in each region via TOPSIS
   (primary) and VIKOR (robustness cross-check) multi-criteria analysis on
   a common 1 km² grid.
2. **Technical potential estimation** (`src/potential/`) — converts
   retained suitable area into installable capacity, annual energy yield,
   and annual hydrogen output per site.
3. **LCOH decomposition** (`src/economics/`) — computes levelised cost of
   hydrogen under actual, WACC-swap counterfactual, and inversion-point
   financing conditions, plus incentive-scenario cases.
4. **Sensitivity analysis** (`src/sensitivity/`) — a one-at-a-time economic
   sensitivity sweep, a global variance-based Sobol sensitivity analysis
   (Saltelli sampling) over WACC/capacity factor/renewable CAPEX, and a
   TOPSIS weight-perturbation robustness check against the VIKOR
   cross-check.

Each stage is runnable and inspectable independently; its output is
written to disk (GeoTIFF, CSV, or JSON) rather than passed silently in
memory to the next stage. See
[02_architecture_and_dataflow.md](02_architecture_and_dataflow.md) for the
concrete module map and execution order.

## Full narrative methodology

For the complete academic write-up of the methodology (research design,
all five stages, and stated limitations), see the root `METHODOLOGY.md`.
This file and its siblings in `docs/memory/` summarize and index that
document for fast orientation; `METHODOLOGY.md` remains the canonical
prose source.
