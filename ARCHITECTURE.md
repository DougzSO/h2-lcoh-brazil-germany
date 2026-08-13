```markdown
# Project Architecture — Hydrogen LCOH Decomposition Pipeline

## Overview

This repository implements a four-stage geospatial-economic pipeline comparing
green hydrogen levelised cost between Northeast Brazil and North Germany. The
pipeline derives candidate hydrogen hub sites from a multi-criteria spatial
suitability analysis, estimates technical hydrogen potential for those sites,
computes levelised cost of hydrogen (LCOH) under actual, counterfactual, and
incentive-adjusted financing conditions, and performs sensitivity analysis
and a 2D competitiveness-frontier search. All input data required by the pipeline is acquired
automatically from public sources, with one-time manual credential setup
required for a single data source (Corine Land Cover).

---

## Design Principles

The architecture mirrors the four analytical stages of the methodology one to
one, with no abstraction layer that does not correspond to an actual
methodological step. Each stage is runnable and inspectable independently, with
its output written to disk as a GeoTIFF, CSV, or JSON file rather than passed
silently in memory to the next stage. Data acquisition is fully isolated from
data processing: every external network request lives in the acquisition layer,
so that a change in a data provider — for example replacing GADM with an
alternative boundary source — requires no change to any downstream spatial,
economic, or sensitivity module. No catch-all utility folder exists; shared, non-domain-specific reprojection
logic (`reproject_and_resample()`, `get_analysis_grid()`) lives in
`spatial/grid_utils.py`. Pixel alignment across layers within a region is
achieved by each consuming module (`spatial/exclusion_mask.py`,
`spatial/topsis.py`) reprojecting directly onto `get_analysis_grid()`'s
exact transform and shape, rather than by a shared cross-layer alignment
function.

---

## Repository Structure

```
project-root/
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── run_pipeline.py
├── test_quick.py
├── config/
│   ├── scenario_params.yaml
│   └── config_loader.py
├── src/
│   ├── core/
│   │   └── constants.py
│   ├── acquisition/
│   │   ├── solar_wind_atlas.py
│   │   ├── srtm.py
│   │   ├── admin_boundaries_fetch.py
│   │   ├── landuse_fetch.py
│   │   ├── protected_areas_fetch.py
│   │   ├── grid_infrastructure_fetch.py
│   │   ├── water_bodies_fetch.py
│   │   └── credentials.py
│   ├── spatial/
│   │   ├── grid_utils.py
│   │   ├── admin_boundaries.py
│   │   ├── data_layers.py
│   │   ├── exclusion_mask.py
│   │   ├── topsis.py
│   │   ├── vikor.py
│   │   ├── site_selection.py
│   │   └── _config_helpers.py
│   ├── potential/
│   │   └── h2_potential.py
│   ├── economics/
│   │   ├── lcoh_model.py
│   │   ├── decomposition.py
│   │   ├── competitiveness_frontier.py
│   │   └── incentive_scenarios.py
│   ├── sensitivity/
│   │   └── sensitivity_analysis.py
│   └── viz/
│       └── plotting.py
├── data/
│   ├── raw/
│   ├── processed/
│   └── boundaries/
├── outputs/
│   ├── maps/
│   ├── tables/
│   └── figures/
└── tests/
    ├── test_lcoh_model.py
    ├── test_topsis.py
    ├── test_grid_utils.py
    └── test_admin_boundaries.py
```

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `core/constants.py` | Defines the `Region` enum and `RegionCRS.projected_crs_for()`, the single source of truth for coordinate reference systems across the pipeline. |
| `acquisition/solar_wind_atlas.py` | Downloads Global Solar Atlas and Global Wind Atlas layers via bulk API. |
| `acquisition/srtm.py` | Retrieves SRTM 30 m elevation data via OpenTopography / AWS Terrain Tiles. |
| `acquisition/admin_boundaries_fetch.py` | Downloads IBGE municipal boundaries for Brazil and GADM boundaries for Germany. |
| `acquisition/landuse_fetch.py` | Downloads MapBiomas Collection 9 for Brazil and Corine Land Cover for Germany via the Copernicus Land Monitoring Service; fails explicitly if the required CDSE credential is absent. |
| `acquisition/protected_areas_fetch.py` | Downloads ICMBio/MMA protected areas for Brazil and Natura 2000 for Germany, clipped to the regional bounding box. |
| `acquisition/grid_infrastructure_fetch.py` | Downloads power transmission infrastructure (lines, cables, substations) for both regions from OpenStreetMap via the Overpass API, matching the acquisition pattern used for surface water bodies. Originally planned as ANEEL SIGA (Brazil) + Marktstammdatenregister (Germany); implemented as a single OSM source instead to avoid two divergent country-specific API integrations — see `docs/memory/03_data_sources_and_acquisition.md`. |
| `acquisition/water_bodies_fetch.py` | Downloads OpenStreetMap waterway data via the Overpass API for the distance-to-water criterion. |
| `acquisition/credentials.py` | Reads one-time manual setup credentials — for example the CDSE token — from environment variables or an untracked `.env` file; fails explicitly with a descriptive error if a required credential is missing. |
| `spatial/grid_utils.py` | Reprojects and resamples any raster layer onto the common 1 km² equal-area analysis grid, identically for both regions. |
| `spatial/admin_boundaries.py` | Reads administrative boundary files already written to `data/raw/` by the acquisition layer and exposes `get_region_bounds(Region)`; performs no network access. |
| `spatial/data_layers.py` | Loads each already-downloaded input layer — including solar, wind, slope, distance-to-grid, and distance-to-water — and returns each reprojected onto the common grid. |
| `spatial/exclusion_mask.py` | Reclassifies land-use rasters into a binary suitable/excluded mask and rasterizes protected-area vectors into a combined exclusion layer per region. |
| `spatial/topsis.py` | Implements the TOPSIS suitability procedure and the weight-perturbation sensitivity function operating on the same criteria and weights. |
| `spatial/vikor.py` | Implements the VIKOR procedure as an independent cross-check against the TOPSIS ranking. |
| `spatial/site_selection.py` | Aggregates the TOPSIS suitability raster to the administrative-unit level via zonal statistics, applies the minimum contiguous suitable-area threshold, and ranks and returns the top candidate sites per region. |
| `spatial/_config_helpers.py` | Maps the `Region` enum to its corresponding YAML configuration key; the only location permitted to perform this translation. |
| `potential/h2_potential.py` | Computes installable capacity, annual energy yield, and annual hydrogen output for each retained site. |
| `economics/lcoh_model.py` | Implements the discounted lifetime LCOH formula as a pure function of capital expenditure, operating expenditure, WACC, lifetime, hydrogen output, stack replacement, water cost, country CAPEX multiplier, electrolyzer type, and optional incentive value and duration. |
| `economics/decomposition.py` | Executes the actual-conditions run, the WACC-swap counterfactual, and the combined min/baseline/max scenario LCOH range (`run_scenario_range()`) for each region/technology pair. |
| `economics/competitiveness_frontier.py` | Computes the 2D WACC_BR x WACC_DE competitiveness frontier per renewable technology (deterministic grid, baseline elsewhere) and the two subsidy-equivalence metrics (WACC-gap closure, direct support); replaces the earlier 1D inversion-point search. |
| `economics/incentive_scenarios.py` | Executes the REHIDRO and IPCEI Hy2Use-consistent incentive scenarios against the actual-conditions baseline. |
| `sensitivity/sensitivity_analysis.py` | Executes the one-at-a-time economic sensitivity sweep — including electrolyzer technology — and orchestrates the TOPSIS weight-perturbation check against the VIKOR cross-check, producing a combined weight-perturbation-versus-VIKOR concordance table. |
| `viz/plotting.py` | Contains all plotting functions used across stages, including suitability maps, the sensitivity tornado chart, and the 2D competitiveness-frontier heatmap. |
| `run_pipeline.py` | Orchestrates the full pipeline in sequence, loading configuration once and writing each stage's output to disk before proceeding; contains no analytical logic. |

---

## Data Flow

```
acquisition/*  ──(writes raw files)──▶  data/raw/
                                              │
                                              ▼
                                 spatial/data_layers.py
                                 spatial/admin_boundaries.py
                                              │
                                 spatial/grid_utils.py
                                 (reprojection onto common 1 km² grid)
                                              │
                                 spatial/exclusion_mask.py
                                              │
                          spatial/topsis.py ──┴── spatial/vikor.py
                                                   (cross-check)
                                              │
                                 spatial/site_selection.py
                                 (zonal aggregation, ranking)
                                              │
                                 potential/h2_potential.py
                                              │
                    economics/lcoh_model.py ──┬── economics/decomposition.py
                                              │   (actual / WACC-swap / combined-scenario range)
                                              ├── economics/competitiveness_frontier.py
                                              │   (2D WACC_BR x WACC_DE frontier)
                                              └── economics/incentive_scenarios.py
                                              │
                                 sensitivity/sensitivity_analysis.py
                                              │
                                 viz/plotting.py
                                              │
                                              ▼
                                 outputs/{maps, tables, figures}
```

---

## Configuration

`config/scenario_params.yaml` holds every numeric parameter used anywhere in
the study:

- WACC values and sensitivity ranges by country and technology
- PEM and alkaline capital expenditure and specific energy consumption
- Fixed operating expenditure and stack replacement percentages
- Country-specific capital expenditure multiplier
- Water cost
- TOPSIS criterion weights
- Minimum contiguous suitable-area threshold (`min_contiguous_suitable_area_km2`)
- Power density by technology (`power_density_mw_per_km2`)
- MapBiomas collection version
- REHIDRO and IPCEI Hy2Use incentive scenario parameters

`config/config_loader.py` loads and validates this file against expected types
and ranges using Pydantic before any other module runs, and returns a single
structured configuration object passed explicitly to every downstream module.
No module downstream reads the YAML file directly.

---

## Dependencies

| Package | Purpose |
|---|---|
| `rasterio` | Raster input/output and coordinate reference system handling |
| `pyproj` | Reprojection and geodetic computations |
| `geopandas` | Administrative boundaries and vector operations |
| `rasterstats` | Zonal statistics in site selection |
| `numpy` | TOPSIS and VIKOR array computations |
| `scipy` | Inversion-point root-finding via `scipy.optimize.brentq` |
| `pydantic` | Configuration validation |
| `matplotlib` | All static plotting output |
| `pandas` | Tabular outputs at every stage after raster computations |
| `requests` | All data acquisition modules |
| `python-dotenv` | Reading one-time manual setup credentials |
| `pytest` | Testing |
```