# 02 — Architecture and Data Flow

**Every module `ARCHITECTURE.md` describes now exists, imports cleanly**
(`test_quick.py`: 30/30 `src/` modules OK), **and `run_pipeline.py` now
orchestrates all of them end to end** via a `--stage
{acquisition,spatial,potential,economics,sensitivity,viz,all}` /
`--region {brazil,germany,all}` / `--skip-acquisition` CLI (see "Pipeline
execution sequence" below and
[08_commands_and_reproducibility.md](08_commands_and_reproducibility.md)).
`tests/` now holds a real, passing, fully-offline pytest suite (65 tests
across 4 files, 0 failures) alongside the pre-existing `test_quick.py`
smoke test. Remaining work, if any, is deepening test coverage and
running the pipeline live once acquisition has network access in some
future environment — see `SPRINT_LOG.md`'s "Next Session Starts Here".

## Module map

| Module | Responsibility |
|---|---|
| `src/core/constants.py` | `Region` enum and `RegionCRS.projected_crs_for()` — single source of truth for coordinate reference systems. |
| `src/acquisition/solar_wind_atlas.py` | Global Solar Atlas + Global Wind Atlas bulk API download. |
| `src/acquisition/srtm.py` | SRTM 30 m elevation via AWS Terrain Tiles / OpenTopography. |
| `src/acquisition/admin_boundaries_fetch.py` | IBGE municipal boundaries (Brazil) + GADM boundaries (Germany); writes `data/boundaries/{region}.geojson`. |
| `src/acquisition/water_bodies_fetch.py` | OSM waterway data via Overpass API; rasterizes and computes distance-to-water. |
| `src/acquisition/credentials.py` | Reads one-time manual-setup credentials (e.g. CDSE token) from environment or `.env`; fails explicitly if missing. |
| `src/acquisition/landuse_fetch.py` | MapBiomas Collection 9 (BR, windowed `/vsicurl/` COG read) + Corine Land Cover (DE, CDSE-token-authenticated WCS); writes `data/raw/landuse/{region}/landuse.tif` in each source's native CRS — reprojection is deferred to `spatial/exclusion_mask.py` since land use is categorical (see `grid_utils.py`'s nearest/mode resampling requirement). No live download verified yet in this environment; see `03_data_sources_and_acquisition.md`. |
| `src/acquisition/protected_areas_fetch.py` | ICMBio/MMA conservation units (BR, INDE GeoServer WFS) + Natura 2000 (DE, EEA discomap ArcGIS REST); both queried with a server-side bbox filter and written directly as `data/raw/protected_areas/{region}/protected_areas.geojson`. No live query verified yet in this environment; see `03_data_sources_and_acquisition.md`. |
| `src/acquisition/grid_infrastructure_fetch.py` | OSM Overpass API power transmission infrastructure (`power=line`, `power=cable`, `power=substation`) for both regions; writes `data/raw/grid/{region}/distance_to_grid.tif`. Deviates from the ANEEL SIGA/Marktstammdatenregister source originally planned in `ARCHITECTURE.md` — see that file and `03_data_sources_and_acquisition.md` for the rationale. |
| `src/spatial/grid_utils.py` | Reprojects/resamples any raster onto the common 1 km² analysis grid; `get_analysis_grid()`, `reproject_and_resample()`, `align_rasters()`. |
| `src/spatial/admin_boundaries.py` | Reads boundary files already written by acquisition layer from `data/boundaries/`; exposes `get_region_bounds()`, `get_dissolved_polygon()`. Performs no network access. |
| `src/spatial/data_layers.py` | Loads each downloaded input layer (solar, wind, slope, distance-to-grid, distance-to-water), reprojected onto the common grid, with unit conversions applied. |
| `src/spatial/_config_helpers.py` | The **only** location permitted to translate `Region` enum → YAML config key (`get_grid_crs()`). |
| `src/spatial/exclusion_mask.py` | `create_exclusion_mask(region, config)` — combines land-use reclassification (nearest-neighbor warp of `landuse_fetch.py`'s output directly onto `get_analysis_grid()`'s transform, bypassing `grid_utils.reproject_and_resample()` to guarantee pixel alignment) AND protected-area rasterization (`protected_areas_fetch.py`'s GeoJSON output); writes `data/processed/exclusion_mask_{region}.tif` (uint8, 1=suitable/0=excluded). Verified only with synthetic fixtures so far; see `04_spatial_methodology.md`. |
| `src/spatial/topsis.py` | `run_topsis(region, tech, config, custom_weights=None)` — vectorized TOPSIS over 4 criteria (resource, slope, distance_to_grid, distance_to_water) restricted to unmasked/finite cells; writes `data/processed/topsis_suitability_{region}_{tech}.tif` (float32, 0.0 for excluded cells). `perturb_topsis_weights()` provides the ±20%-style weight sensitivity used by `sensitivity_analysis.py`. Re-aligns each `data_layers.py` criterion onto `get_analysis_grid()`'s exact grid itself (same alignment fix `exclusion_mask.py` applied to land use). Maps `config.topsis.weights`' 4 field names onto the 4 criteria via a documented best-effort correspondence — see `04_spatial_methodology.md`. Verified extensively offline, not yet against real criterion rasters. |
| `src/spatial/vikor.py` | `run_vikor(region, tech, config, v=0.5, custom_weights=None)` — vectorized VIKOR (compromise index $Q_i$, converted to suitability $=1-Q_i$) over the SAME 4 criteria as `topsis.py`, reusing `topsis.CRITERIA`/`topsis.CRITERION_DIRECTION`/`topsis._load_criterion()`/`topsis._get_default_weights()` directly rather than re-implementing criteria loading; writes `data/processed/vikor_suitability_{region}_{tech}.tif` (float32, 0.0 for excluded cells). `compute_concordance(topsis_scores, vikor_scores, mask, top_k_pct=0.10)` returns Spearman rho/p-value plus a Jaccard top-k%-overlap between a TOPSIS and a VIKOR raster — the robustness cross-check METHODOLOGY.md §2.3 describes. Verified extensively offline (including side-by-side agreement with `run_topsis()` on synthetic inputs), not yet against real criterion rasters. |
| `src/spatial/site_selection.py` | `select_candidate_sites(region, tech, config, top_n=None)` — reads `topsis_suitability_{region}_{tech}.tif` + `exclusion_mask_{region}.tif` from `data/processed/` (not re-run); 8-connectivity clustering (`scipy.ndimage.label`) of suitable cells, drops clusters below `config.thresholds.min_contiguous_suitable_area_km2`, ranks survivors by `mean_suitability * log1p(suitable_area_km2)`; writes `data/processed/candidate_sites_{region}_{tech}.{geojson,csv}`. Aggregates per contiguous cluster (via `rasterio.features.shapes`), not per administrative unit — `admin_boundaries.py`'s own docstring documents `get_dissolved_polygon()` (always one polygon per region) as this module's boundary source; a per-admin-unit `zonal_stats` branch exists for a future finer-grained boundary source but is not exercised today. Verified end-to-end with real synthetic GeoTIFF fixtures, not yet against real criterion rasters — see `04_spatial_methodology.md`. |
| `src/potential/h2_potential.py` | `calculate_h2_potential(region, tech, config, electrolyzer_type="pem")` — reads `candidate_sites_{region}_{tech}.geojson` from `data/processed/` (written by `site_selection.py`, not re-run); computes `installable_capacity_mw = suitable_area_km2 * power_density_mw_per_km2`, `annual_electricity_yield_mwh = installable_capacity_mw * full_load_hours`, `annual_h2_production_kg/_t` via `config.electrolyzer.technologies[electrolyzer_type].efficiency_kwh_per_kg`; writes `data/processed/h2_potential_{region}_{tech}.{geojson,csv}` (geometry never dropped). `run_all_potential(config, electrolyzer_type="pem")` runs all region×tech pairs. Capacity density, full-load hours, and capacity factor are all resolved region-specifically via `economics/decomposition.py`'s shared resolver functions, reading `config.technologies.<solar_pv\|onshore_wind>.<brazil\|germany>` `{min, baseline, max}` ranges through `resolve_param()` at `config.scenario.active` — see `09_methodology_assumptions.md` / `10_capacity_density_assumptions.md`. Output filename does not encode `electrolyzer_type` — see `04_spatial_methodology.md`. Verified with synthetic candidate-sites fixtures, not yet against real site_selection.py output. |
| `src/economics/lcoh_model.py` | Pure function `calculate_lcoh()` (discounted lifetime LCOH) and `calculate_lcoe()` (renewable LCOE, fed into LCOH's required electricity-cost term). No project imports — standard library only. |
| `src/economics/decomposition.py` | `decompose_actual()`, `decompose_wacc_swap()`, `find_inversion_point()` (via `scipy.optimize.brentq`), `run_all_decompositions()`. |
| `src/economics/incentive_scenarios.py` | `run_rehidro_scenario()` (Brazil only), `run_ipcei_scenario()` (Germany only), `run_all_incentive_scenarios()` — now also persists its result to `outputs/tables/incentive_scenarios.csv` (one row per region) before returning, so `viz/plotting.py`'s `generate_all_plots()` can find it without any caller passing the dict through by hand. |
| `src/sensitivity/sensitivity_analysis.py` | `run_sensitivity(config)` — economic one-at-a-time sweep (WACC, electrolyzer CAPEX, electrolyzer efficiency, PEM-vs-alkaline, CAPEX multiplier) via `calculate_lcoh()`; writes `outputs/tables/economic_sensitivity.csv`. `run_mcda_sensitivity(region, tech, config, delta_pct=0.20)` — TOPSIS weight-perturbation vs. VIKOR concordance table (orchestrates `topsis.perturb_topsis_weights()` / `run_topsis()` / `vikor.run_vikor()` / `vikor.compute_concordance()`, none re-implemented); writes `outputs/tables/mcda_sensitivity_concordance_{region}_{tech}.csv`. `run_sobol_analysis(config)` — global variance-based Sobol sensitivity analysis (Saltelli sampling via SALib) over WACC, capacity_factor, and renewable CAPEX (the 3 parameters with a literature-validated `{min, baseline, max}`/range in `scenario_params.yaml`; electrolyzer CAPEX/OPEX and capacity_density are structurally excluded — see ADR-006 in [06_technical_decisions_log.md](06_technical_decisions_log.md)); N=1,024 base samples, 5,120 `calculate_lcoh()` evaluations per region/tech pair; writes `outputs/tables/sobol_{region}_{renewable_tech}.csv` (columns: `parameter, S1, S1_conf, ST, ST_conf, interaction`). `run_all_sensitivities(config)` runs all three (economic OAT, MCDA, Sobol) for every region x tech. Full rationale and verified results: [05_economic_model.md](05_economic_model.md#sobol-global-sensitivity-analysis-srcsensitivity). |
| `src/viz/plotting.py` | Pure-visualization layer, matplotlib-only (`Agg` backend, 300 DPI, `bbox_inches="tight"`), never re-computes an analytical result and never imports `src/economics/`, `src/sensitivity/`, or `src/spatial/`'s analytical modules. `plot_suitability_map()` / `plot_candidate_sites_map()` read `data/processed/*.tif` + `*.geojson` + `data/boundaries/{region}.geojson`, writing to `outputs/maps/`. `plot_lcoh_decomposition()` / `plot_inversion_point()` / `plot_sensitivity_tornado()` / `plot_incentive_scenarios()` take an already-computed DataFrame/Dict (or read a fixed `outputs/tables/*.csv` path), writing to `outputs/figures/`. `generate_all_plots(config)` orchestrates all of the above, skipping (warning, not raising) any plot whose prerequisite file doesn't exist yet — it never calls an analytical module to fill the gap. |
| `run_pipeline.py` | Full CLI orchestrator: `--stage {acquisition,spatial,potential,economics,sensitivity,viz,all}` (repeatable, fixed dependency order regardless of flag order), `--region {brazil,germany,all}`, `--skip-acquisition`. Contains **no analytical logic** — every stage function is a thin wrapper around already-implemented `src/` public functions. Every stage that depends on live-acquired data (acquisition, spatial, potential, sensitivity's MCDA half) catches per-region/per-technology failures individually and reports "ok"/"skipped"/"failed" in a JSON run summary rather than crashing the whole run; economics, sensitivity's economic half, and viz are pure config-driven (or already self-defensive) and always succeed. |
| `config/scenario_params.yaml` + `config/config_loader.py` | Single source of truth for every numeric parameter, loaded and validated via Pydantic (`ScenarioConfig`). |

> ⚠️ Point to validate: the module list above reflects `SPRINT_LOG.md`'s
> "Module Status Snapshot" at the time this file was written. Before relying
> on any module's status (empty / partial / complete), re-check
> `SPRINT_LOG.md` and the file itself — this snapshot decays quickly during
> active development.

## Pipeline execution sequence

`run_pipeline.py` runs six named stages (`acquisition`, `spatial`,
`potential`, `economics`, `sensitivity`, `viz`) via `--stage <name>`
(repeatable) or `--stage all`, always in this fixed dependency order
regardless of the order flags are passed — `config` (loading/validating
`scenario_params.yaml`) is not itself a `--stage` choice; it always runs
first, implicitly, since every other stage needs it:

```
acquisition/*  ──(writes raw files)──▶  data/raw/, data/boundaries/
                                              │
                        stage: spatial ──▶ spatial/admin_boundaries.py
                                            spatial/data_layers.py
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
                     stage: potential ──▶ potential/h2_potential.py
                                              │
                    economics/lcoh_model.py ──┬── economics/decomposition.py
             stage: economics ──▶            │   (actual / WACC-swap / inversion search)
                                              └── economics/incentive_scenarios.py
                                              │
                  stage: sensitivity ──▶ sensitivity/sensitivity_analysis.py
                                              │
                        stage: viz ──▶ viz/plotting.py
                                              │
                                              ▼
                                 outputs/{maps, tables, figures}
```

Every stage function in `run_pipeline.py` wraps the underlying `src/`
calls it makes with per-region/per-technology error handling (see the
module map row above) — a missing upstream input (expected without live
network access to acquisition sources) is reported as `"skipped"` in the
stage's JSON summary, never an uncaught crash that aborts the rest of the
run. `economics`, `sensitivity`'s economic-OAT half, and `viz` never
depend on live-acquired raster/vector data and are expected to always
succeed; `python run_pipeline.py --stage economics --stage sensitivity
--stage viz` is therefore always safe to run in an offline environment.

## Strict disk-based intermediate persistence

Every stage writes its output to disk (GeoTIFF, CSV, JSON, or GeoJSON)
**before** the next stage reads it. No stage passes data to the next
silently in memory across process boundaries. This makes every stage:

- independently runnable and inspectable (`python run_pipeline.py --stage
  spatial` produces artifacts you can open without running anything
  downstream),
- resumable without re-fetching or re-computing upstream stages,
- verifiable via the stage-by-stage commands in
  [08_commands_and_reproducibility.md](08_commands_and_reproducibility.md).

## Acquisition isolation rule

**Every network request in the entire codebase lives in `src/acquisition/`.**
No other module — spatial, economics, potential, sensitivity, viz — makes a
network call. Each acquisition module exposes a uniform contract:
`fetch(region, config) -> Path`. This isolation means:

- replacing a data provider (e.g. GADM → BKG VG250 for Germany) requires no
  change to any downstream spatial, economic, or sensitivity module;
- `data/raw/` and `data/boundaries/` are written **only** by acquisition
  modules — never by hand, never by a spatial or economics module;
- downstream modules that need boundary or raster data always read from
  disk (`spatial/admin_boundaries.py`, `spatial/data_layers.py`), never
  from the network, which is what makes `test_quick.py` and `pytest`
  runnable offline once acquisition has populated `data/`.

See [ADR-001](06_technical_decisions_log.md) for the rationale, and
[03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md)
for the concrete provider list and paths.
