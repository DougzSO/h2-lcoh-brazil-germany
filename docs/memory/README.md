# Persistent Memory — Index

This directory is the persistent, disk-based memory system for the Hydrogen
LCOH Decomposition Pipeline (Northeast Brazil vs. North Germany). It exists
because the codebase, configuration, and methodology are large enough that
no single working session — human or automated — can hold all of it in
context at once. Each file below is a modular, domain-scoped reference.
Read only what the current task requires; do not load the entire directory
for a narrow change.

## Recommended reading order

For a **new investigation, refactor, or new pipeline stage**, read in this
order:

1. [01_project_overview.md](01_project_overview.md) — what the study is and why it exists.
2. [02_architecture_and_dataflow.md](02_architecture_and_dataflow.md) — module map and execution order.
3. The file matching the layer being touched:
   - [03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md) for `src/acquisition/`
   - [04_spatial_methodology.md](04_spatial_methodology.md) for `src/spatial/`
   - [05_economic_model.md](05_economic_model.md) for `src/economics/`, `src/potential/`, `src/sensitivity/`
   - [09_methodology_assumptions.md](09_methodology_assumptions.md) / [10_capacity_density_assumptions.md](10_capacity_density_assumptions.md) specifically for capacity factor / capacity density parameters within those same modules
4. [06_technical_decisions_log.md](06_technical_decisions_log.md) — check whether an ADR already governs the change you're about to make.
5. [07_risks_and_limitations.md](07_risks_and_limitations.md) — known gaps, before treating any result as final.
6. [08_commands_and_reproducibility.md](08_commands_and_reproducibility.md) — how to run and verify whatever you touched.

For a **quick fact lookup** (a single parameter value, a single module's
responsibility), jump directly to the relevant file instead of reading in
order.

## How to validate assumptions

Documentation drifts from code. Real code in `src/`, `config/`, and `tests/`
is always the final source of truth — see the source-of-truth rule in the
root `CLAUDE.md`. Several statements in this directory are flagged as
provisional, either because the underlying implementation decision is still
open, or because a numeric value is a placeholder pending confirmation from
a primary source. These are marked inline as:

> ⚠️ Point to validate: <statement, and what would resolve it>

Before relying on a flagged statement for a publishable result, code change,
or new module, resolve it against the current state of the relevant source
file, `config/scenario_params.yaml`, or `SPRINT_LOG.md`, and update the flag
(or remove it) once resolved. Do not silently propagate a flagged value into
new code without carrying the flag forward.

## Maintenance rule

Any structural, methodological, or configuration change to the pipeline
must update the relevant file(s) in this directory, and this index, in the
same unit of work that makes the change — see root `CLAUDE.md`. A memory
file that no longer matches the code it describes is worse than no memory
file at all.

## Files in this directory

| File | Scope |
|---|---|
| [01_project_overview.md](01_project_overview.md) | Research question, regional pair rationale, four-stage structure |
| [02_architecture_and_dataflow.md](02_architecture_and_dataflow.md) | Module map, execution sequence, disk-persistence and acquisition-isolation rules |
| [03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md) | Data providers, raw/processed paths, unit conversions |
| [04_spatial_methodology.md](04_spatial_methodology.md) | Analysis grid, CRS, exclusion masking, TOPSIS/VIKOR, site aggregation |
| [05_economic_model.md](05_economic_model.md) | LCOH formula, electrolyzer specs, CAPEX multiplier, WACC-swap, inversion search, incentive scenarios |
| [06_technical_decisions_log.md](06_technical_decisions_log.md) | Architecture Decision Records (ADR-001 through ADR-005) |
| [07_risks_and_limitations.md](07_risks_and_limitations.md) | Known limitations and data asymmetries |
| [08_commands_and_reproducibility.md](08_commands_and_reproducibility.md) | Running the pipeline, smoke tests, pytest, stage verification |
| [09_methodology_assumptions.md](09_methodology_assumptions.md) | Capacity factor literature review + study-specific `{min, baseline, max}` ranges per region/technology, combined-scenario semantics (no `scenario.active` selector any more) |
| [10_capacity_density_assumptions.md](10_capacity_density_assumptions.md) | Capacity density (MW/km²) literature review + study-specific `{min, baseline, max}` ranges per technology |
