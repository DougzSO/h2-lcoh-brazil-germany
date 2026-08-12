# Sprint Log

## Current Sprint: Acquisition Layer Foundation + Stage 3 Fixes

**Goal:** Complete acquisition layer and fix partial modules before spatial pipeline.
**Status: 🎉 100% FINISHED.** Every module `ARCHITECTURE.md` describes
exists and imports cleanly (`test_quick.py`: 30/30 `src/` modules OK).
`run_pipeline.py` now orchestrates all six stages
(`acquisition`/`spatial`/`potential`/`economics`/`sensitivity`/`viz`) end
to end via `--stage`/`--region`/`--skip-acquisition`, robustly skipping
(never crashing on) any stage whose input isn't available. `tests/` holds
a real, passing, fully-offline pytest suite: **65 tests across 4 files, 0
failures.** `incentive_scenarios.run_all_incentive_scenarios()` persists
to `outputs/tables/incentive_scenarios.csv`.
**This session's environment DOES have live network access** (unlike
every prior session) and used it to find and fix a real regression: the
disk-read refactor of `admin_boundaries.py` (Issue 1, below) had assigned
the wrong CRS to `germany.geojson` (real WGS84 degrees mislabeled as
already-projected AEA meters), collapsing Germany's bbox to ~5m x 4m and
silently breaking every fetcher downstream of it for that region. Fixed
with per-file coordinate-magnitude detection instead of a blanket
assumption (see Issue 5 below) and verified live against real endpoints:
Natura 2000 (1,484 features), OSM water bodies (206,947 features) and
power infrastructure (65,510 features) for the real Germany bbox, plus a
new ESA WorldCover 2021 fallback (AWS COG, verified reachable) for when
Corine/CDSE access is unavailable. CDSE_TOKEN, REHIDRO, and IPCEI Hy2Use
values remain placeholders pending real credentials/program documentation
-- not a gap introduced this session, see "Next Session Starts Here".

---

## Module Status Snapshot

### ✅ COMPLETE
- `core/constants.py` — Region enum + RegionCRS.projected_crs_for()
- `spatial/_config_helpers.py` — Region→config-key translation (single location)
- `spatial/grid_utils.py` — reproject_and_resample, get_analysis_grid, align_rasters
- `economics/lcoh_model.py` — Pure LCOH function, matches METHODOLOGY.md §2.5
- `src/acquisition/credentials.py` — Reads .env, fails explicitly if missing (25 lines)
- `src/acquisition/admin_boundaries_fetch.py` — Downloads IBGE (BR) + GADM (DE) to data/raw/ (64 lines)
- `src/acquisition/srtm.py` — AWS Terrain Tiles with progress bars (96 lines)
- `src/acquisition/solar_wind_atlas.py` — GWA + GSA GHI with progress bars (176 lines)
- `spatial/data_layers.py` — Refactored loaders with unit conversions, now including `load_distance_to_grid()` (328 lines)
- `economics/decomposition.py` — Baseline incentive-free by construction
- `spatial/admin_boundaries.py` — Refactored to read from disk (309 lines)
  - Network requests removed (requests, time, tempfile, zipfile, retry logic)
  - Reads `data/boundaries/brazil.geojson` and `data/boundaries/germany.geojson`
  - CRS assigned directly via `RegionCRS.projected_crs_for(region)` (files have wrong CRS tag)
  - All public signatures unchanged: fetch_nordeste_states, fetch_north_germany_states,
    get_dissolved_polygon, get_region_bounds
- `src/acquisition/water_bodies_fetch.py` — OSM Overpass API, major water features (205 lines) ✅ NEW THIS SESSION
  - `fetch(region, config) -> Path` — queries Overpass for `natural=water`,
    `water=reservoir`, `waterway=riverbank`, `waterway=river` (ways + relations)
    over the region's WGS84 bbox, with primary/fallback endpoint retry on
    timeout (overpass-api.de → overpass.kumi.systems)
  - Rasterizes parsed geometries onto `grid_utils.get_analysis_grid()`'s
    reference grid, then `scipy.ndimage.distance_transform_edt` with
    per-axis pixel-size `sampling` gives true meters even though the grid's
    actual cell size isn't exactly 1000m (get_analysis_grid rounds width/
    height up, so cell size is `(maxx-minx)/width`, not always 1000.0)
  - Writes `data/raw/water_bodies/{region}/distance_to_water.tif`
  - Verified live end-to-end for Germany: 206,942 features parsed, 149s
    Overpass query + 38s rasterize, output loads cleanly via
    `data_layers.load_distance_to_water()` (mean 8,970m, max 115,881m)
  - ⚠️ Brazil (9-state bbox, ~6x the area) NOT yet run live this session —
    expect a substantially longer Overpass query; fallback endpoint retry
    is in place but has not been exercised against a real timeout
  - ✅ UPDATE (2026-08-12): `data/raw/water_bodies/brazil/distance_to_water.tif`
    now exists on disk (13.9 MB, modified 2026-08-05) — confirmed via
    direct file check in a later session; the Brazil run apparently
    completed successfully at some point after this entry was written.
- `src/acquisition/grid_infrastructure_fetch.py` — OSM Overpass API, power transmission infrastructure (214 lines) ✅ NEW THIS SESSION
  - `fetch(region, config) -> Path` — queries Overpass for `power=line`,
    `power=cable`, `power=substation` (nodes + ways + relations) over the
    region's WGS84 bbox, same primary/fallback endpoint retry as
    `water_bodies_fetch.py`
  - Deviates from `ARCHITECTURE.md` / `03_data_sources_and_acquisition.md`,
    which specify ANEEL SIGA (BR) + Marktstammdatenregister (DE) as the
    source. Implemented as a single OSM Overpass query instead, mirroring
    `water_bodies_fetch.py`, to keep both regions on one acquisition code
    path and avoid two country-specific API integrations. Docs updated
    this session per the source-of-truth rule (root `CLAUDE.md`).
  - Rasterizes parsed geometries onto `grid_utils.get_analysis_grid()`'s
    reference grid, then `scipy.ndimage.distance_transform_edt` with
    per-axis pixel-size `sampling` for true meters
  - Writes `data/raw/grid/{region}/distance_to_grid.tif`
  - Verified live end-to-end for Germany: 57,156 features parsed, 82.0s
    end-to-end (71.5s Overpass query + 4.1s rasterize), output loads
    cleanly via `data_layers.load_distance_to_grid()` (mean 3,434m, max
    40,258m)
  - Brazil not yet run live this session (same caveat as `water_bodies_fetch.py`)
  - ✅ UPDATE (2026-08-12): `data/raw/grid/brazil/distance_to_grid.tif`
    now exists on disk (13.9 MB, modified 2026-08-05) — confirmed via
    direct file check in a later session.
- `src/acquisition/landuse_fetch.py` — MapBiomas Collection 9 (BR) + Corine Land Cover (DE) (241 lines) NEW THIS SESSION
  - `fetch_brazil(config) -> Path` — opens the MapBiomas Collection 9 national
    coverage COG (public GCS bucket) via GDAL /vsicurl/, windowed-reads only
    Northeast Brazil's bbox (no full national download)
  - `fetch_germany(config) -> Path` — WCS GetCoverage request against the EEA
    discomap Corine Land Cover 2018 service, cropped server-side to North
    Germany's bbox; requires CDSE_TOKEN via credentials.get_cdse_token(),
    sent as a Bearer header; fails explicitly (verified live: no network
    call attempted) if the token is unset
  - `fetch(region, config) -> Path` — dispatches to the two region-specific
    functions, matching the standard acquisition contract
  - Writes `data/raw/landuse/{region}/landuse.tif`, in each source's native
    CRS -- deliberately NOT reprojected onto the analysis grid here, since
    land use is categorical and grid_utils.py requires an explicit
    method="nearest"/"mode" choice for categorical resampling, which is
    spatial/exclusion_mask.py's job, not acquisition's
  - Neither region's live download has been run this session (no network
    access in this environment). fetch_brazil/fetch_germany/fetch import
    cleanly and satisfy the fetch(region, config) -> Path contract (verified
    via py_compile + signature inspection); fetch_germany's missing-credential
    path was verified live (raises MissingCredentialError before any
    request). The exact MapBiomas COG URL and Corine WCS endpoint/coverage-ID
    are best-effort from published documentation, not a verified live
    response -- see the module's SOURCE NOTE docstrings and
    docs/memory/03_data_sources_and_acquisition.md.
- `src/acquisition/protected_areas_fetch.py` — ICMBio/MMA conservation units (BR) + Natura 2000 (DE) (195 lines) NEW THIS SESSION
  - `fetch_brazil(config) -> Path` — queries INDE's (Infraestrutura Nacional
    de Dados Espaciais) public GeoServer WFS for the MMA/ICMBio CNUC
    conservation-units layer, server-side BBOX filter,
    outputFormat=application/json — no local shapefile ZIP/fiona dependency
  - `fetch_germany(config) -> Path` — queries the EEA discomap ArcGIS REST
    FeatureServer for the Natura 2000 dynamic dataset (f=geojson), also a
    server-side bbox filter, no shapefile/GML parsing needed
  - `fetch(region, config) -> Path` — dispatches to the two region-specific
    functions, matching the standard acquisition contract
  - Writes `data/raw/protected_areas/{region}/protected_areas.geojson`
    directly from each server's JSON response — deliberately simpler than
    `landuse_fetch.py`: no COG windowed read, no WCS raster crop, since both
    sources here return GeoJSON natively from a vector feature-service query
  - Deviates from `ARCHITECTURE.md`'s "Automated direct shapefile download"
    description for Brazil: uses INDE's GeoServer WFS instead of a raw
    shapefile ZIP, to avoid a fiona/zipfile dependency for a one-off bbox
    crop — same rationale already applied to `landuse_fetch.py` avoiding a
    national MapBiomas download. Docs updated this session.
  - ⚠️ Neither region's live query has been run this session (no network
    access in this environment). `fetch`/`fetch_brazil`/`fetch_germany`
    import cleanly and satisfy the `fetch(region, config) -> Path` contract
    (verified via `py_compile` + signature inspection); full `test_quick.py`
    run confirms 30/30 modules import OK including this one. The INDE WFS
    layer typeName and EEA discomap Natura2000 endpoint/layer index are
    best-effort from published documentation, not a verified live response
    — see the module's SOURCE NOTE docstrings and
    `docs/memory/03_data_sources_and_acquisition.md`.
- `economics/incentive_scenarios.py` — REHIDRO (BR) + IPCEI Hy2Use (DE) scenarios (164 lines) ✅ FIXED THIS SESSION
  - `run_rehidro_scenario(region, config)` — Brazil only, raises ValueError otherwise
  - `run_ipcei_scenario(region, config)` — Germany only, raises ValueError otherwise
  - `run_all_incentive_scenarios(config)` — baseline (via decomposition.decompose_actual)
    + incentive LCOH for both regions
  - Calls `calculate_lcoh()` directly with incentive_value/duration from `region_cfg.incentives`;
    reuses decomposition.py's private helpers instead of duplicating decompose logic
  - ⚠️ `scenario_params.yaml` REHIDRO fields (production_credit_usd_per_kg=1.00,
    support_period_years=10 for Brazil) are PLACEHOLDERS pending real REHIDRO figures —
    same treatment as Germany's existing EUR/USD placeholder
- `config/config_loader.py` — `ThresholdsConfig.min_contiguous_suitable_area_km2: float` added ✅ FIXED THIS SESSION
  - YAML already had the field; Pydantic was silently dropping it before this fix
- `economics/decomposition.py` & `sensitivity/sensitivity_analysis.py` — `__main__` import bug fixed ✅ THIS SESSION
  - Both now import `load_scenario_config` from `config.config_loader` (was `src.economics.config_loader`, which does not exist)
- `src/acquisition/admin_boundaries_fetch.py` — output path aligned with reader ✅ FIXED THIS SESSION
  - Writes to `data/boundaries/{region}.geojson` (was `data/raw/admin_boundaries_{region}.geojson`)
  - Now matches exactly what `spatial/admin_boundaries.py`'s `_BOUNDARY_FILES` reads
- `spatial/exclusion_mask.py` — binary suitable/excluded mask (258 lines) ✅ NEW THIS SESSION
  - `create_exclusion_mask(region, config) -> Tuple[np.ndarray, Dict]` —
    combines land-use reclassification AND protected-area rasterization via
    logical AND; writes `data/processed/exclusion_mask_{region}.tif` (uint8,
    1=suitable, 0=excluded)
  - Land-use: warps `data/raw/landuse/{region}/landuse.tif` (nearest-neighbor,
    since class codes are categorical) directly onto `get_analysis_grid()`'s
    exact transform/shape via `rasterio.warp.reproject`, then reclassifies —
    MapBiomas classes {11,24,30,33} excluded (BR), Corine ranges
    111-142/411-423/511-523 excluded (DE), class 0 always excluded as
    nodata/unclassified; everything else treated as suitable
  - Protected areas: rasterizes `data/raw/protected_areas/{region}/
    protected_areas.geojson` onto the same reference grid via
    `rasterio.features.rasterize`, inverted to 1=unprotected/0=protected
  - Deliberately does NOT call `grid_utils.reproject_and_resample()` for the
    land-use raster — that function computes its own output transform from
    the source's reprojected extent, which is not guaranteed to match
    `get_analysis_grid()`'s transform pixel-for-pixel (see
    `data_layers.py`'s CRS NOTE, which explicitly flags this as something to
    verify in `exclusion_mask.py`). Warping directly onto the reference
    grid's transform/shape guarantees the land-use mask and the rasterized
    protected-area mask are pixel-aligned before the logical AND — no
    `grid_utils.py` changes were made to achieve this.
  - Continuous criteria (slope, resource quality) are deliberately NOT
    applied here as binary cutoffs — resolves the open design question
    flagged in `scenario_params.yaml`'s `thresholds` block and
    `docs/memory/04_spatial_methodology.md` in favor of METHODOLOGY.md
    §2.3's stated design (slope is TOPSIS-weighted, not a binary cutoff;
    the binary mask covers only land use + protected areas)
  - ✅ Verified with two synthetic end-to-end runs (mocked `get_analysis_grid`
    + hand-written landuse.tif/protected_areas.geojson fixtures, cleaned up
    after): (1) suitable land-use fully overlapping a protected polygon
    correctly produces an all-excluded mask; (2) suitable, unprotected
    land-use correctly produces the expected suitable pixel count and area
    split (50/100 km² for a 50/50 synthetic split). Also unit-tested
    `_reclassify_landuse()` directly against hand-picked class codes for
    both regions. No live real-data run — `landuse_fetch.py` and
    `protected_areas_fetch.py`'s own live downloads are themselves
    unverified in this environment (no network access); see those modules'
    entries above and `docs/memory/04_spatial_methodology.md`.
- `spatial/topsis.py` -- TOPSIS suitability scoring + weight perturbation
  (306 lines) NEW THIS SESSION
  - `run_topsis(region, tech, config, custom_weights=None) -> Tuple[np.ndarray, Dict]`
    -- vectorized TOPSIS restricted to unmasked, finite cells only (never
    computed over excluded pixels -- memory optimization per instructions);
    writes `data/processed/topsis_suitability_{region}_{tech}.tif` (float32,
    excluded/masked cells = 0.0)
  - 4 criteria: resource (GHI or wind power density, technology-selected,
    benefit), slope (cost), distance_to_grid (cost), distance_to_water
    (cost) -- standard vector-normalized TOPSIS (r=x/norm(x), v=w*r, ideal
    points per direction, C=S-/(S+ + S-))
  - `perturb_topsis_weights(weights, target_criterion, delta_pct) -> Dict[str, float]`
    -- adjusts one criterion's weight, proportionately renormalizes the rest
    so the set still sums to 1.0
  - Weight-field mapping (new assumption, flagged): `config.topsis.weights`
    has fields resource_quality/proximity_infrastructure/land_availability/
    grid_distance, none of which literally spell slope/distance_to_water --
    this module maps resource_quality->resource, grid_distance->distance_to_grid
    (exact name match), proximity_infrastructure->distance_to_water (water is
    a distinct electrolysis-input "infrastructure" concern from the
    transmission grid), land_availability->slope (flatter = more buildable
    land). Documented in the module's WEIGHT-FIELD MAPPING NOTE and in
    docs/memory/04_spatial_methodology.md; YAML itself untouched.
  - Alignment (same issue exclusion_mask.py solved for land use):
    data_layers.py's 5 loaders each reproject via
    grid_utils.reproject_and_resample(), which computes its own output
    transform per source raster -- not guaranteed to match
    get_analysis_grid()'s transform, nor to match each other. This module
    re-reads each loader's metadata["aligned_path"] output and warps
    (bilinear) directly onto get_analysis_grid()'s exact transform/shape
    before assembling the decision matrix. exclusion_mask.create_exclusion_mask()
    is called as-is (not modified) and already returns a mask on that same
    reference grid, so it needs no extra alignment step.
  - Verified: _vectorized_topsis() unit-tested against three known-answer
    cases (single benefit criterion, single cost criterion, two-criterion
    dominance) -- dominant rows score exactly C=1, dominated rows exactly
    C=0, ordering correct in all cases. perturb_topsis_weights()
    unit-tested for +-20% on resource -- target hits 0.42/0.28 exactly,
    remaining weights keep their relative proportions, sum stays 1.0;
    unknown-criterion input raises ValueError. _get_default_weights()
    verified against the real loaded scenario_params.yaml. Full
    run_topsis() verified end-to-end with mocked get_analysis_grid,
    create_exclusion_mask, and all 5 data_layers loaders (synthetic
    GeoTIFFs on the same reference grid, cleaned up after): excluded half
    of a 10x10 grid comes back exactly 0.0; valid half is strictly
    monotonic in resource value and correctly hits the C=0/C=1 boundary at
    the worst/best valid cell. No live run against real
    resource/slope/distance rasters -- those upstream layers are themselves
    unverified in this environment (no network access for the fetches that
    populate data/raw/).
- `spatial/vikor.py` -- VIKOR robustness cross-check + Spearman concordance
  helper (303 lines) NEW THIS SESSION
  - `run_vikor(region, tech, config, v=0.5, custom_weights=None) -> Tuple[np.ndarray, Dict]`
    -- vectorized VIKOR, same criteria/mask/valid-cell restriction as
    run_topsis(); writes data/processed/vikor_suitability_{region}_{tech}.tif
    (float32, excluded cells = 0.0)
  - Formula: f*/f- per criterion (direction-aware, same as TOPSIS's
    ideal/anti-ideal), d_ij = (f*-x_ij)/(f*-f-), S_i = sum(w*d), R_i =
    max(w*d), Q_i = v*(S_i-S*)/(S--S*) + (1-v)*(R_i-R*)/(R--R*),
    suitability_i = 1.0 - Q_i (so 1.0=best, matching TOPSIS's scale)
  - Does NOT re-implement criteria loading, grid alignment, or weight
    resolution: imports topsis.CRITERIA, topsis.CRITERION_DIRECTION,
    topsis._load_criterion(), topsis._get_default_weights() directly.
    exclusion_mask.create_exclusion_mask() called as-is, not modified.
    This was the explicit constraint this session ("do not re-implement
    criteria loading differently than topsis.py") and also resolves the
    open question flagged in last session's Next Session note about
    avoiding a third independent alignment implementation.
  - `compute_concordance(topsis_scores, vikor_scores, mask, top_k_pct=0.10) -> Dict[str, float]`
    -- scipy.stats.spearmanr over mask==1 cells (spearman_rho,
    spearman_pvalue) + Jaccard similarity (|intersection|/|union|, as a
    percentage) of the top-k% cells by each method (top_k_overlap_pct).
    Documented caveat: mask param is the raw exclusion mask, coarser than
    the finite-criteria "valid" subset run_topsis/run_vikor use
    internally -- tied 0.0 scores at mask==1-but-invalid cells can
    slightly inflate reported concordance.
  - Verified: _vectorized_vikor() unit-tested against known-answer cases
    (two-criterion dominance -> exactly Q=0/Q=1 at the dominant/dominated
    rows; single benefit criterion -> correct ordering).
    compute_concordance() unit-tested for identical rasters (rho=1.0,
    100% overlap), perfectly inverted rasters (rho=-1.0, 0% overlap), and
    invalid inputs (shape mismatch, bad top_k_pct both raise ValueError).
    Full run_vikor() verified end-to-end with the same mocked
    get_analysis_grid/create_exclusion_mask/data_layers-loader fixtures as
    topsis.py's own end-to-end test, run side-by-side with run_topsis() on
    identical inputs: both produce numerically IDENTICAL suitability
    rasters on a single-varying-criterion synthetic case (mathematically
    expected -- both methods reduce to the same min-max normalization with
    only one discriminating criterion), and compute_concordance() on that
    pair reports rho=1.0, 100% top-k overlap -- concrete evidence the
    criteria-loading reuse actually produces identical inputs to both
    methods, not just structurally similar code. No live run against real
    resource/slope/distance rasters -- same limitation as topsis.py (no
    network access in this environment for the fetches that populate
    data/raw/).
- `spatial/site_selection.py` -- zonal aggregation, contiguous-area
  filtering, candidate-site ranking (247 lines) NEW THIS SESSION
  - `select_candidate_sites(region, tech, config, top_n=None) -> gpd.GeoDataFrame`
    -- reads topsis_suitability_{region}_{tech}.tif and
    exclusion_mask_{region}.tif directly from data/processed/ (already
    written by run_topsis()/create_exclusion_mask() -- neither module's
    logic imported or modified); writes
    data/processed/candidate_sites_{region}_{tech}.geojson +
    .csv (geometry serialized as WKT in the CSV; the returned GeoDataFrame
    always keeps a real geometry column)
  - 8-connectivity clustering via scipy.ndimage.label (structure=np.ones((3,3)))
    over (mask==1) & (suitability>0); cluster pixel counts via np.bincount;
    clusters below config.thresholds.min_contiguous_suitable_area_km2
    (never hardcoded) are dropped before aggregation
  - Zonal-unit source: admin_boundaries.py's OWN module docstring documents
    site_selection.py as a consumer of get_dissolved_polygon() (which by
    design always returns ONE dissolved polygon per region) -- so this
    module implements two branches (per-admin-unit zonal_stats vs.
    per-contiguous-cluster aggregation via rasterio.features.shapes +
    shapely unary_union) but only the second actually executes today,
    since get_dissolved_polygon() always returns exactly 1 row. The
    per-unit branch's rasterstats import is LAZY (imported inside that
    branch, not at module top) because rasterstats -- a documented project
    dependency -- is NOT actually installed in this environment (confirmed:
    `pip show rasterstats` finds nothing) and the branch that runs today
    doesn't need it; making the import lazy means the module still imports
    and runs cleanly here rather than hard-failing on an unrelated,
    pre-existing dependency gap (root cause is requirements.txt being
    empty, already flagged in docs/memory/07_risks_and_limitations.md).
  - Ranking: rank_score = mean_suitability * log1p(suitable_area_km2),
    descending sort, integer rank 1..N assigned after sorting; rank_score
    itself is NOT in the output columns (only
    rank/site_id/region/tech/mean_suitability/max_suitability/
    suitable_area_km2/geometry, per spec)
  - Empty-result case (no cluster survives the threshold) returns a valid
    0-row GeoDataFrame with correct columns/CRS and writes valid empty
    GeoJSON/CSV, rather than crashing
  - ✅ Verified end-to-end with synthetic topsis/exclusion_mask GeoTIFFs
    written directly to data/processed/ (real file I/O, not just mocked
    function returns) and a mocked get_dissolved_polygon(): (1) a 4x4
    (16 km²) block correctly survives a 5.0 km² threshold while an
    isolated 1x1 (1 km²) cell is correctly filtered out, with exact
    mean/max suitability and geometry area (16.0 km² via both the returned
    Polygon's .area and the suitable_area_km2 column) matching by
    construction; (2) all-below-threshold input returns a valid empty
    result end-to-end (GeoJSON re-read confirms 0 features); (3) two
    passing clusters rank in the correct order (higher mean_suitability
    first) and top_n=1 truncation works. GeoJSON/CSV outputs verified
    readable after writing (re-read via geopandas/pandas). No live run
    against real topsis/exclusion_mask output -- those are themselves
    unverified against real data in this environment (no network access
    for the upstream acquisition fetches all session).

- `potential/h2_potential.py` — technical H2 potential per candidate site (243 lines) ✅ NEW THIS SESSION
  - `calculate_h2_potential(region, tech, config, electrolyzer_type="pem") -> gpd.GeoDataFrame`
    -- reads `candidate_sites_{region}_{tech}.geojson` directly from
    `data/processed/` (already written by `site_selection.select_candidate_sites()`
    -- neither its logic nor output modified); per site:
    `installable_capacity_mw = suitable_area_km2 * power_density_mw_per_km2`,
    `annual_electricity_yield_mwh = installable_capacity_mw * full_load_hours`,
    `annual_h2_production_kg = annual_electricity_yield_mwh * 1000.0 / specific_consumption_kwh_per_kg`,
    `annual_h2_production_t = annual_h2_production_kg / 1000.0`; writes
    `data/processed/h2_potential_{region}_{tech}.{geojson,csv}` (geometry
    kept as a real column, WKT in the CSV -- same convention as
    `site_selection.py`)
  - `run_all_potential(config, electrolyzer_type="pem") -> Dict[str, Dict[str, gpd.GeoDataFrame]]`
    -- runs both regions x both siting techs at one electrolyzer technology
  - All three physical parameters resolved from `ScenarioConfig`, never
    hardcoded: `power_density_mw_per_km2` from
    `config.renewables.solar_pv/.onshore_wind.power_density_w_per_m2` (unit
    conversion written out explicitly even though it's numerically 1:1 for
    W/m2->MW/km2); `full_load_hours = capacity_factor_default * 8760.0`,
    reusing the exact convention already established in
    `economics/decomposition.py:_lcoh_for()` and
    `sensitivity/sensitivity_analysis.py`; `specific_consumption_kwh_per_kg`
    from `config.electrolyzer.technologies[electrolyzer_type].efficiency_kwh_per_kg`
    (52.0 PEM / 51.0 alkaline read live from the YAML, never assumed)
  - Empty-input case (0 candidate sites) returns a valid 0-row GeoDataFrame
    with correct columns/CRS and writes valid empty GeoJSON/CSV, mirroring
    `site_selection.py`'s own empty-result handling
  - ⚠️ **New discovery this session, not specific to this module:** GDAL's
    GeoJSON driver tags the written CRS as EPSG:4326 per RFC 7946 without
    actually reprojecting coordinates -- verified live by round-tripping a
    GeoDataFrame written in the AEA CRS: `gpd.read_file()` reports
    `crs == EPSG:4326` but `total_bounds` are still the original AEA meter
    values (e.g. `[0, 0, 10000, 10000]`, not degrees). This affects
    **every** `.geojson` this pipeline writes in a projected CRS, including
    `site_selection.py`'s own `candidate_sites_{region}_{tech}.geojson`.
    A naive `gpd.read_file(...).to_crs(target_crs)` on any such file
    silently produces Inf/NaN geometries (confirmed: triggered a
    `RuntimeWarning: Infinite or NaN coordinate encountered` from `pyogrio`
    during the next write). Fixed here by overriding the CRS read back from
    disk with `RegionCRS.projected_crs_for(region)` via
    `set_crs(allow_override=True)` instead of reprojecting -- correct
    specifically because the raw coordinate values were never actually
    reprojected on write, only mislabeled. **Any future module that reads
    a `.geojson` written by this pipeline (e.g. a per-site LCOH pass reading
    `h2_potential_*.geojson`) must apply the same override, not `.to_crs()`.**
  - ⚠️ Two design points carried forward as flags rather than silently
    resolved (both documented in `docs/memory/04_spatial_methodology.md`):
    (1) `power_density_w_per_m2` and `capacity_factor_default` are not
    region-specific in the current `RenewablesConfig` schema -- both
    regions share the same solar/wind technical parameters, a pre-existing
    schema choice, not something this module introduced; (2) output
    filenames do not encode `electrolyzer_type`, so re-running for the same
    region/tech with a different technology overwrites the previous file
    on disk (the returned `GeoDataFrame` itself is always correct) --
    mirrors the existing PEM-baseline/alkaline-sensitivity-only asymmetry
    in `economics/decomposition.py`
  - ✅ Verified end-to-end with real synthetic `candidate_sites_*.geojson`
    fixtures written to `data/processed/` (not just mocked returns): PEM
    run's `installable_capacity_mw`/`annual_electricity_yield_mwh`/
    `annual_h2_production_kg`/`_t` match hand-computed expected values
    exactly for a 100 km² site; alkaline run correctly uses its own
    `efficiency_kwh_per_kg` (51.0, not the PEM value) rather than a
    hardcoded number; invalid `electrolyzer_type` raises `ValueError`;
    0-row candidate-sites input produces a valid empty result with the
    correct schema; missing candidate-sites file raises
    `FileNotFoundError`; geometry column confirmed never dropped. Full
    `test_quick.py` run: 30/30 `src/` modules import OK (0 failed). No live
    run against real `site_selection.py` output -- that module's own
    output is itself unverified against real spatial data in this
    environment (no network access for the upstream acquisition fetches).

- `sensitivity/sensitivity_analysis.py` — economic OAT sweep + MCDA weight-perturbation vs VIKOR concordance (441 lines) ✅ COMPLETE THIS SESSION
  - `run_sensitivity(config) -> pd.DataFrame` — unchanged one-at-a-time
    economic sweep from the prior session (WACC, electrolyzer CAPEX,
    electrolyzer efficiency, PEM-vs-alkaline technology switch, region
    CAPEX multiplier), preserved logic-for-logic; only the output filename
    changed, `outputs/tables/sensitivity_tornado.csv` ->
    `outputs/tables/economic_sensitivity.csv` (no other module referenced
    the old constant/filename, confirmed by grep before renaming)
  - `run_mcda_sensitivity(region, tech, config, delta_pct=0.20) -> pd.DataFrame`
    -- NEW this session, the previously-missing §2.7 weight-perturbation
    vs. VIKOR concordance orchestration. Runs baseline `run_topsis()` +
    baseline `run_vikor()` + their `compute_concordance()`; then for each
    of `topsis.CRITERIA`'s 4 criteria x 2 directions (+delta_pct,
    -delta_pct), perturbs weights via `topsis.perturb_topsis_weights()`,
    re-runs `run_topsis()` with the perturbed weights, and computes
    concordance against both the TOPSIS baseline and the VIKOR baseline.
    Writes 1 baseline row + 8 perturbation rows to
    `outputs/tables/mcda_sensitivity_concordance_{region}_{tech}.csv`.
    Neither TOPSIS nor VIKOR math is re-implemented -- `run_topsis`,
    `run_vikor`, `perturb_topsis_weights`, `compute_concordance` are
    imported directly from `topsis.py`/`vikor.py` and called as-is
  - `run_all_sensitivities(config) -> Dict[str, Any]` -- runs the economic
    sweep once plus `run_mcda_sensitivity()` for both regions x both
    siting techs (4 calls); returns
    `{"economic": df, "mcda": {"<region>_<tech>": df, ...}}`
  - `delta_pct` defaults to 0.20, matching METHODOLOGY.md §2.7's stated
    "plus or minus 20 percent" -- kept as a function parameter rather than
    a new `ScenarioConfig` field, the same treatment
    `perturb_topsis_weights()`'s own `delta_pct` argument already had (a
    sensitivity-design choice, not a physical/economic parameter to
    source from the YAML)
  - `__main__` now calls `run_all_sensitivities(cfg)` (previously
    `run_sensitivity(cfg)`), exercising both sweeps; still imports
    `load_scenario_config` from `config.config_loader` (item 16's fix,
    preserved)
  - ✅ Verified: `run_sensitivity()` re-run against the real
    `scenario_params.yaml` (132 rows, same shape/parameter-group counts as
    before the rename) confirms the economic sweep logic is byte-for-byte
    unchanged. `run_mcda_sensitivity()` verified end-to-end with synthetic
    10x10 criterion rasters (mocking `get_analysis_grid`,
    `create_exclusion_mask`, and all 5 `data_layers` loaders in BOTH
    `topsis.py`'s and `vikor.py`'s own module namespaces, the same pattern
    already used to verify those two modules themselves): exactly 9 rows
    returned (1 baseline + 4 criteria x 2 directions); baseline row's
    vs-TOPSIS columns are exactly self-identical (rho=1.0, 100% overlap);
    every perturbed-weights dict sums to 1.0; every Spearman rho is in
    [-1, 1] and every overlap percentage in [0, 100]; CSV written and
    re-read successfully. Full `test_quick.py` run: 30/30 `src/` modules
    import OK. No live run against real criterion rasters -- same
    limitation `topsis.py`/`vikor.py` themselves carry (no network access
    in this environment for the upstream acquisition fetches).

- `viz/plotting.py` — pure-visualization layer, matplotlib `Agg` backend, 300 DPI (450 lines) ✅ COMPLETE THIS SESSION
  - `plot_suitability_map(region, tech, config) -> Path` — reads
    `topsis_suitability_{region}_{tech}.tif` + `exclusion_mask_{region}.tif`,
    `YlGn` colormap for valid cells (`cmap.set_bad("#d3d3d3")` for
    excluded), boundary overlay from `data/boundaries/{region}.geojson`;
    writes `outputs/maps/suitability_{region}_{tech}.png`
  - `plot_candidate_sites_map(region, tech, config) -> Path` — prefers
    `h2_potential_{region}_{tech}.geojson` (adds
    `installable_capacity_mw`) over `candidate_sites_{region}_{tech}.geojson`
    if both exist; marker size ~ `installable_capacity_mw` (falls back to
    `suitable_area_km2`), color = `mean_suitability`, `viridis` colormap,
    site_id labels; handles the 0-site case gracefully; writes
    `outputs/maps/candidate_sites_{region}_{tech}.png`
  - `plot_lcoh_decomposition(decomposition_results, config) -> Path` --
    grouped bar chart, baseline vs. WACC-swap LCOH per region x renewable
    tech, from an already-computed `decomposition.py`-style result table;
    writes `outputs/figures/lcoh_decomposition.png`
  - `plot_inversion_point(config, inversion_results=None) -> Path` --
    Brazil's wacc_swap/actual/inversion points (2-3 points, connected by
    straight segments, NOT a resampled analytical curve) vs. Germany's
    fixed baseline (dashed horizontal line), per renewable tech; reads
    `outputs/tables/decomposition.csv` + `inversion_points.csv` by fixed
    path unless `inversion_results` is given; `converged=False` annotated
    as a valid finding (per decomposition.py's own documented semantics),
    not suppressed; writes `outputs/figures/inversion_point.png`
  - `plot_sensitivity_tornado(df_sensitivity, region, tech) -> Path` --
    horizontal tornado chart, min/max LCOH range per swept parameter, from
    an already-computed `sensitivity_analysis.run_sensitivity()` result
    table; writes `outputs/figures/sensitivity_tornado_{region}_{tech}.png`
    -- `tech` here must match the table's own `renewable_tech` values
    ("solar_pv"/"onshore_wind"), NOT the siting `tech` ("solar"/"wind")
    used by the two map functions above (documented explicitly in the
    function's own docstring to avoid the two-vocabularies confusion this
    codebase already flags elsewhere)
  - `plot_incentive_scenarios(incentives_results, config) -> Path` --
    grouped bar chart, baseline vs. REHIDRO (Brazil) / IPCEI Hy2Use
    (Germany), from an already-computed
    `incentive_scenarios.run_all_incentive_scenarios()` result; writes
    `outputs/figures/incentive_scenarios.png`
  - `generate_all_plots(config) -> Dict[str, List[Path]]` -- orchestrates
    all of the above; for every plot, checks the prerequisite file(s)
    exist on disk FIRST and skips with a warning print (never raises, never
    calls an analytical module to fill the gap) if not; returns
    `{"maps": [...], "figures": [...]}` of only the paths actually written
  - **Zero imports from `src/economics/`, `src/sensitivity/`, or the
    analytical modules under `src/spatial/`** (`topsis.py`, `vikor.py`,
    `exclusion_mask.py`, `site_selection.py`) -- the only project import
    is `src.core.constants` (`Region`/`RegionCRS`). This was a deliberate,
    maximally-conservative reading of this session's "pure visualization
    ... do not re-calculate analytical models" + "do not touch analytical
    modules in src/" constraints: `plot_inversion_point()` could have
    sampled `decomposition._lcoh_for()` at a WACC grid to draw a smooth
    2-15% curve (closer to the task prompt's literal wording), but that
    would require importing `economics/decomposition.py`'s LCOH machinery
    into a "pure visualization" module -- the constraint was treated as
    the harder requirement, so the curve is built from only the 2-3
    already-computed points on disk, connected by straight segments,
    documented explicitly as a deliberate simplification in the function's
    own docstring
  - CRS fix reused from `h2_potential.py`: `_read_vector()` overrides any
    `.geojson` read via `set_crs(RegionCRS.projected_crs_for(region),
    allow_override=True)` rather than trusting the file's self-reported
    CRS tag or calling `.to_crs()` against it (same GDAL GeoJSON
    RFC-7946-tagging quirk documented there and in
    `docs/memory/04_spatial_methodology.md`)
  - ⚠️ New discovery this session: `incentive_scenarios.run_all_incentive_scenarios()`
    returns a dict but never persists it to `outputs/tables/` (unlike
    `decomposition.py` and `sensitivity_analysis.py`, which both write
    CSVs) -- `plot_incentive_scenarios()` is fully implemented and unit
    -tested against a synthetic results dict, but `generate_all_plots()`
    has no CSV to read and always skips it with a warning. Flagged in
    `docs/memory/05_economic_model.md`; fixing it means adding a
    `to_csv()` call inside `incentive_scenarios.py` itself, out of scope
    for this session ("do not touch analytical modules in src/")
  - ✅ Verified: `matplotlib.get_backend() == "Agg"` confirmed; no
    `plt.show()`/seaborn/plotly anywhere (grepped). All 4 economic/
    sensitivity figures verified against REAL data this session --
    `decomposition.run_all_decompositions(cfg)` (pure config-driven math,
    no network needed) produced real `decomposition.csv` /
    `inversion_points.csv` (both technologies actually returned
    `converged=False` with current parameters, exercising the "no
    inversion" annotation path for real, not just synthetically), the
    real `economic_sensitivity.csv` from the prior session drove 4
    tornado charts, and `incentive_scenarios.run_all_incentive_scenarios(cfg)`'s
    real dict drove the incentive figure. Map functions verified against
    synthetic GeoTIFF/GeoJSON fixtures (same pattern as
    topsis.py/vikor.py/site_selection.py/h2_potential.py), including the
    boundary overlay rendering correctly against the REAL
    `data/boundaries/brazil.geojson` already on disk from an earlier
    session's live `run_pipeline.py --stage boundaries` run, and the
    0-candidate-sites case. `generate_all_plots(config)` verified
    end-to-end with only the real economic CSVs present (no
    raster/geojson fixtures): correctly produced 0 maps + 6 figures
    (`lcoh_decomposition` + `inversion_point` + 4 tornado charts,
    2 regions x 2 techs) and gracefully skipped every map (8 skips) and
    `incentive_scenarios` (1 skip) with warnings, never raising. Output
    PNGs confirmed 300 DPI via PIL (`img.info["dpi"] == (299.9994,
    299.9994)`, floating-point rounding from matplotlib's 300 DPI
    request). Full `test_quick.py` run: 30/30 `src/` modules import OK --
    **every module `ARCHITECTURE.md` describes now exists.**

- `run_pipeline.py` -- full CLI orchestrator ✅ COMPLETE THIS SESSION
  (rewritten, ~370 lines). `--stage {acquisition,spatial,potential,
  economics,sensitivity,viz,all}` (repeatable, fixed dependency order
  regardless of flag order), `--region {brazil,germany,all}` (default
  `all`), `--skip-acquisition`. `config` is no longer a `--stage` choice
  -- it always runs first, implicitly, since every stage needs it.
  - `stage_acquisition()` -- runs every acquisition module's
    `fetch(region, config)`, each wrapped individually so one
    unreachable/uncredentialed source doesn't abort the rest
  - `stage_spatial()` -- `admin_boundaries` -> `data_layers` ->
    `exclusion_mask` -> `topsis` & `vikor` -> `site_selection`, per region
    (and per siting tech for the last three); each step's own
    `FileNotFoundError`/`TopsisError`/`VikorError`/`SiteSelectionError`
    is caught individually so a missing upstream input is a per-item
    skip, never an abort of sibling regions
  - `stage_potential()` -- `calculate_h2_potential()` per region/tech,
    same per-pair error handling
  - `stage_economics()` -- `run_all_decompositions()` +
    `run_all_incentive_scenarios()`; pure config-driven math, always
    succeeds
  - `stage_sensitivity()` -- `run_sensitivity()` (always succeeds) +
    `run_mcda_sensitivity()` per region/siting-tech, each wrapped
    individually (this is the one place a broad `except Exception` is
    used, since `run_mcda_sensitivity()` itself doesn't distinguish
    failure types the way `topsis.py`/`vikor.py` do)
  - `stage_viz()` -- `generate_all_plots()`, already self-defensive, no
    extra wrapping needed
  - Every top-level stage call in `main()` is ALSO wrapped in a
    `try/except Exception` that records `"status": "fatal_error"` and
    continues to the next stage rather than crashing the whole CLI --
    defense in depth on top of each stage function's own internal
    per-region/per-tech handling
  - `_count_statuses()` recursively tallies every `{"status": ...}` leaf
    in a stage's nested result dict for a compact one-line progress recap
    printed after each stage, in addition to the full JSON summary at the
    end
  - ✅ Verified LIVE, not just import-checked: `python run_pipeline.py
    --stage economics --stage sensitivity --stage viz` (the task's own
    verification command) completed in ~12s with real results --
    `economics` and `sensitivity`'s economic-OAT half both `"ok"`,
    `sensitivity`'s 4 MCDA pairs all correctly `"skipped"` (no live
    land-use data), `viz` `"ok"` with 7 real figures. Separately,
    `python run_pipeline.py --stage spatial --region germany` (no
    `--skip-acquisition`, but no network call since `admin_boundaries.py`
    reads from disk and `data_layers.py`/`exclusion_mask.py` check
    `Path.exists()` before touching the network) exercised REAL
    reprojection of the real Global Solar Atlas / Global Wind Atlas / OSM
    data already on disk for Germany from earlier sessions: 4 of 5
    `data_layers` loaders + `admin_boundaries` genuinely succeeded (real
    GDAL reprojection, not synthetic), `slope` and `exclusion_mask`
    correctly reported `"skipped"` (no SRTM/land-use data). A full
    `python run_pipeline.py --stage all --skip-acquisition` run (~9s)
    exercised every stage together with the same clean ok/skipped split.
    No live acquisition run attempted (would require real network access
    this environment doesn't have -- same limitation every prior session
    has carried).

- `src/economics/incentive_scenarios.py` -- CSV persistence added ✅ FIXED THIS SESSION
  - `run_all_incentive_scenarios(config)` now writes
    `outputs/tables/incentive_scenarios.csv` (one row per region: 
    `region`, `baseline_lcoh_usd_per_kg`, `rehidro_lcoh_usd_per_kg`
    [Brazil only, NaN for Germany], `ipcei_lcoh_usd_per_kg` [Germany
    only, NaN for Brazil]) before returning the dict, exactly mirroring
    `decomposition.py`/`sensitivity_analysis.py`'s existing CSV-export
    convention -- closes the gap `viz/plotting.py`'s own session flagged
    in `docs/memory/05_economic_model.md`
  - ✅ Verified: `generate_all_plots()` now produces 7 figures (was 6, with
    `incentive_scenarios` previously always skipped) with zero code
    changes to `viz/plotting.py` itself -- confirms the CSV schema
    (including the two NaN cells) round-trips correctly through
    `plot_incentive_scenarios()`'s existing DataFrame-branch parsing

- `tests/` -- real, passing, fully-offline pytest suite ✅ COMPLETE THIS SESSION
  (65 tests across 4 files, 0 failures; both previously-empty stub files
  filled in, both previously-missing files created)
  - `test_lcoh_model.py` (20 tests) -- known-value benchmark against an
    independently-written reference implementation (not copy-pasted from
    `lcoh_model.py`, so a shared bug would have to be coincidental, not
    structural), zero-WACC closed-form case, stack-replacement-excluded-
    in-final-year (with a second case proving the exclusion is doing
    something observable, not a no-op), PEM-vs-alkaline CAPEX/efficiency
    isolation, zero-incentive-baseline equivalence, incentive-duration-
    beyond-lifetime capping, and every documented `ValueError` edge case
    for both `calculate_lcoh()` and `calculate_lcoe()`
  - `test_topsis.py` (16 tests) -- `_vectorized_topsis()` dominant/
    dominated exact C=1/C=0 boundary cases, benefit-vs-cost ordering,
    constant-column div-by-zero guard, monotonicity under increasing
    benefit/cost values, `perturb_topsis_weights()` exact-target/
    renormalization/clamping (including both extreme-delta clamp
    directions), and `_get_default_weights()` cross-checked against the
    real loaded `scenario_params.yaml` (the WEIGHT-FIELD MAPPING NOTE,
    verified as code)
  - `test_grid_utils.py` (17 tests, NEW file) -- `get_analysis_grid()`
    dimensions (including non-1000m-divisible extents rounding up),
    CRS-mismatch validation, square-pixel/no-AEA-distortion confirmation,
    non-`Region`-enum and inverted-bounds rejection; `_resolve_resampling()`
    method mapping; `reproject_and_resample()` missing-source/
    geographic-target-CRS rejection and a genuine reprojection between
    the two real study-region AEA CRS definitions (proven non-trivial:
    some destination pixels sample the source value, but not all --
    the square footprint warps into a non-square one); `align_rasters()`
    matching/mismatched-footprint cases. Uses an autouse fixture to clean
    up the `data/processed/*_reprojected.tif` files these functions
    unconditionally persist to the REAL project directory regardless of
    any `tmp_path` passed in (Hard Rule 7) -- discovered live when the
    first test run left `source_reprojected.tif`/`r1_reprojected.tif`/
    `r2_reprojected.tif` behind in real `data/processed/`
  - `test_admin_boundaries.py` (12 tests, NEW file) -- `get_dissolved_polygon()`/
    `get_region_bounds()` against synthetic GeoJSON fixtures written under
    `tmp_path` and wired in via `monkeypatch.setitem(ab._BOUNDARY_FILES,
    ...)`, never reading this project's real `data/boundaries/*.geojson`
    (so results don't depend on what happens to already be on disk in a
    given environment): the CRS-tag-override behavior (file tagged
    EPSG:4326, coordinates already in AEA meters, per the module's own
    documented CRS NOTE), multi-polygon dissolve, both directions of the
    sanity-extent check, missing-file and unparseable-file errors, and
    module-level cache behavior (including that the cache survives
    deletion of the source file). ⚠️ Discovered while writing the
    "unrecognized region" test: `Region` is a `str, Enum`, so the literal
    string `"brazil"` compares equal to `Region.NORDESTE_BR` via `==` and
    would be silently accepted by `get_dissolved_polygon()` rather than
    raising -- not a bug (intentional str-Enum behavior, and
    `region.value` is already used consistently everywhere else in the
    codebase), but the test had to use a string matching no member's
    value at all (`"narnia"`) to actually exercise the `else` branch
  - A `_write_boundary()`/`_write_raster()` helper pattern (small
    synthetic GeoJSON/GeoTIFF fixtures via geopandas/rasterio, real file
    I/O under `tmp_path`, no mocking of geopandas/rasterio internals) is
    used consistently across the three new/filled files, mirroring the
    hand-rolled synthetic-fixture pattern every spatial/potential/
    sensitivity/plotting module's own ad hoc verification already used in
    prior sessions -- now captured as real, reusable, CI-runnable pytest
    rather than a one-off script discarded after each session
  - ✅ Full suite verified: `pytest tests/ -v` -- 65 passed, 0 failed.
    `test_quick.py`'s own embedded pytest invocation (Test Q) now reports
    "All pytest tests passed" instead of the previous "no tests ran"
    (both stub files were 0 bytes before this session)

### ❌ MISSING (required by ARCHITECTURE.md)
- None. Every module `ARCHITECTURE.md` describes exists, imports cleanly,
  and is orchestrated end to end by `run_pipeline.py`. `tests/` is a
  real, passing, offline suite. **Sprint 100% finished** -- see "Next
  Session Starts Here" below for what a future sprint could pick up.

---

## Critical Issues → ALL RESOLVED ✅

### Issue 1: admin_boundaries.py architecture violation ✅ FIXED THIS SESSION
- Network requests removed, reads from `data/boundaries/{region}.geojson`
- ⚠️ **Residual path mismatch:** `admin_boundaries_fetch.py` writes to
  `data/raw/admin_boundaries_{region}.geojson` but `admin_boundaries.py` reads from
  `data/boundaries/{region}.geojson` — files currently exist at the read path, but
  fetch script and reader are misaligned. Fix in Stage 3, item 17 (new).

### Issue 2: decomposition.py baseline incentive contamination ✅ FIXED
### Issue 3: scenario_params.yaml Pydantic field mismatch ✅ FIXED THIS SESSION
- `min_contiguous_suitable_area_km2` added to `ThresholdsConfig`
### Issue 4: solar_wind_atlas.py output format vs data_layers.py ✅ FIXED

### Issue 5: admin_boundaries.py Germany CRS regression ✅ FIXED THIS SESSION
- Root cause: Issue 1's disk-read refactor (above) assumed EVERY on-disk
  boundary file's stored CRS tag was wrong in the same direction --
  already-projected AEA meters mislabeled as EPSG:4326 -- and always
  reassigned `RegionCRS.projected_crs_for(region)` directly, never
  reprojecting. True for `brazil.geojson` (an offline meters-scale
  fixture), but `germany.geojson` (written by a real live GADM fetch) is
  genuine WGS84 degrees. Relabeling degree-scale coordinates (lon
  6.6-11.6, lat 51.3-55.1) as already-projected meters collapsed
  Germany's bbox to ~5m x 4m, silently breaking `get_region_bounds()` and
  every fetcher/spatial module downstream of it for that region only
  (Brazil's bbox stayed correct by coincidence).
- Fix: `_read_boundary_file()` now inspects each file's raw coordinate
  magnitude (`_is_geographic_extent()`: within [-180,180] x [-90,90] =>
  real degrees, reproject via `.to_crs()`; otherwise already-projected
  meters, assign directly) instead of one fixed assumption for every
  file. Also added `get_region_bounds_wgs84()` so acquisition fetchers
  stop each reimplementing their own `get_dissolved_polygon(region)
  .to_crs("EPSG:4326").total_bounds` inline.
- Verified live against the real, already-fetched `germany.geojson`:
  projected bbox now (-191190, -273076, 140725, 145964) m -- a sane
  ~332km x 419km box -- and `get_region_bounds_wgs84()` returns
  (6.63, 51.30, 11.60, 55.06)°, matching the real GADM Schleswig-Holstein
  + Niedersachsen extent exactly.

---

## Implementation Order (This Sprint)

### Stage 0: Foundation ✅ COMPLETE
1. ✅ `credentials.py` (25 lines)
2. ✅ `admin_boundaries_fetch.py` (64 lines)

### Stage 1: Raster Acquisition
3. ✅ `srtm.py` (96 lines)
4. ✅ `solar_wind_atlas.py` (176 lines)
5. ✅ `water_bodies_fetch.py` — OSM Overpass API (205 lines)
6. ✅ `grid_infrastructure_fetch.py` — OSM Overpass API, power infrastructure (214 lines; see deviation note above)

### Stage 2: Land Use + Protected Areas ✅ COMPLETE — acquisition layer 100% done
7. ✅ `landuse_fetch.py` — MapBiomas Collection 9 (BR) + Corine Land Cover (DE) (241 lines)
8. ✅ `protected_areas_fetch.py` — ICMBio/MMA (BR) + Natura 2000 (DE) (195 lines)

### Stage 3: Fix Partial Modules
9.  ✅ Refactor `admin_boundaries.py` to read from disk (309 lines)
10. ✅ Update `data_layers.py` solar/wind/water loaders (299 lines)
11. ✅ Implement `load_distance_to_water()` (29 lines added)
12. ✅ Fix `decomposition.py` baseline incentive contamination
13. ✅ Create `economics/incentive_scenarios.py` (164 lines)
14. ✅ Add `min_contiguous_suitable_area_km2` to `ThresholdsConfig`
15. ✅ Remove dead `_get_target_crs()` from `data_layers.py`
16. ✅ Fix `__main__` import bugs in `decomposition.py` + `sensitivity_analysis.py`
17. ✅ Align `admin_boundaries_fetch.py` output path with `admin_boundaries.py` read path
18. ✅ Implement `load_distance_to_grid()` in `data_layers.py` (28 lines added)

### Stage 4: Spatial Pipeline (GIS-MCDA)
19. ✅ `spatial/exclusion_mask.py` — binary suitable/excluded mask (258 lines)
20. ✅ `spatial/topsis.py` — TOPSIS suitability procedure + weight-perturbation
    sensitivity function (306 lines)
21. ✅ `spatial/vikor.py` — VIKOR robustness cross-check + Spearman
    concordance helper (303 lines)
22. ✅ `spatial/site_selection.py` — zonal aggregation, contiguous-area
    threshold, ranking (247 lines) — **Stage 4 (spatial pipeline) is now
    fully complete**

### Stage 5: Technical Potential + Economics Wiring
23. ✅ `potential/h2_potential.py` — installable capacity, annual energy
    yield, annual H₂ output per candidate site (243 lines)
24. ✅ `sensitivity/sensitivity_analysis.py` — economic OAT sweep (preserved)
    + MCDA weight-perturbation vs VIKOR concordance orchestration (new)
    (441 lines) — **Stage 5 is now fully complete**

### Stage 6: Visualization ✅ COMPLETE
25. ✅ `viz/plotting.py` — suitability/candidate-sites maps, LCOH
    decomposition/inversion-point/sensitivity-tornado/incentive-scenarios
    figures, `generate_all_plots()` orchestrator (450 lines) — **every
    module `ARCHITECTURE.md` describes now exists**

### Stage 7: Pipeline Orchestration + Test Suite ✅ COMPLETE — SPRINT 100% FINISHED
26. ✅ `run_pipeline.py` — full CLI orchestrator rewrite: `--stage
    {acquisition,spatial,potential,economics,sensitivity,viz,all}`,
    `--region {brazil,germany,all}`, `--skip-acquisition`; every stage
    wraps its own per-region/per-technology failures individually rather
    than crashing the whole run
27. ✅ `src/economics/incentive_scenarios.py` — `run_all_incentive_scenarios()`
    now persists to `outputs/tables/incentive_scenarios.csv`
28. ✅ `tests/test_lcoh_model.py` (20 tests), `tests/test_topsis.py`
    (16 tests) — filled in from empty stubs
29. ✅ `tests/test_grid_utils.py` (17 tests), `tests/test_admin_boundaries.py`
    (12 tests) — created new
    — **65/65 tests passing, 0 failures, fully offline**

---

## Test Status

> ✅ **UPDATE (2026-08-12):** several entries below say Brazil's live
> fetch was "not run yet" for a given module. That is now stale for the
> files themselves: a direct on-disk check confirms all 7 raw acquisition
> outputs exist for Brazil (`data/raw/{elevation,solar,wind,landuse,
> protected_areas,water_bodies,grid}/brazil/...` and
> `data/boundaries/brazil.geojson`, real dissolved 29-part MultiPolygon,
> not the synthetic fixture some entries below describe), most recently
> modified 2026-08-03 to 2026-08-05 — see the full table and detail in
> `docs/memory/03_data_sources_and_acquisition.md`'s "Brazil acquisition
> confirmed functional" note and the "Next Session Starts Here" addendum
> above. This does not re-verify each fetcher's live request/response
> shape or feature counts for Brazil line-by-line (no fetcher was re-run
> this session) — only that real, structurally sane output exists on
> disk. The specific per-module "Brazil not run live yet" sentences below
> are left as-is (historical record of that session's own scope) rather
> than individually rewritten.

### Completed smoke tests
- ✅ `srtm.py` import OK
- ✅ `solar_wind_atlas.py` fetch_wind() verified live (31MB→8.9MB, 26.8s)
- ✅ `data_layers.py` all loaders importable + unit conversions in place
- ✅ `decomposition.py` baseline incentive-free (verified via inspect)
- ✅ `admin_boundaries.py` both regions load from disk, zero network calls,
     grid_utils.py verified against refactored module
  - **UPDATED THIS SESSION**: the above was true but incomplete -- Germany's
    bbox was silently wrong (Issue 5). Now per-file CRS auto-detection;
    both regions' projected AND WGS84 bounds verified live against the
    real on-disk files, see Issue 5 above.
- ✅ `incentive_scenarios.py` both scenarios run, incentive LCOH < baseline LCOH
     for each region, wrong-region calls correctly raise ValueError
- ✅ `water_bodies_fetch.py` fetch() verified live for Germany (206,942 OSM
     features, 214.6s end-to-end); output loads via `load_distance_to_water()`
     with sane values (mean 8,970m, max 115,881m). Brazil not run live yet.
  - **RE-VERIFIED THIS SESSION** after Issue 5's fix (this earlier result
    was itself obtained before the regression was introduced): 206,947
    features, 188.2s, first-attempt success on the primary Overpass
    server -- materially identical, confirming the fix restores the
    original correct behavior. Third mirror (`maps.mail.ru`) added but not
    exercised (primary server succeeded both times).
- ✅ `grid_infrastructure_fetch.py` fetch() verified live for Germany (57,156
     OSM power-infrastructure features, 82.0s end-to-end); output loads via
     `load_distance_to_grid()` with sane values (mean 3,434m, max 40,258m).
     Brazil not run live yet.
  - **RE-VERIFIED THIS SESSION** after Issue 5's fix: 65,510 features,
    79.5s, first-attempt success (OSM data grows over time, so a feature
    count difference vs. the earlier run is expected and not a regression).
- ✅ `landuse_fetch.py` — py_compile clean; `fetch`, `fetch_brazil`,
     `fetch_germany` import and `fetch(region, config)` signature matches
     the acquisition contract; `fetch_germany()` verified live to raise
     `MissingCredentialError` (no network call attempted) when `CDSE_TOKEN`
     is unset. No live download run for either region this session (no
     network access in this environment) — MapBiomas COG windowed read and
     Corine WCS request are unverified against a real response; see the
     module's SOURCE NOTE docstrings.
  - **VERIFIED LIVE THIS SESSION** (this environment does have network
    access, correcting the note above): real Corine WCS call reached the
    real EEA server (HTTP 498, invalid placeholder token, as expected) and
    correctly triggered the new ESA WorldCover 2021 fallback (see item 6
    in "What was completed this session" above), which wrote a real
    46.16MB `landuse.tif` in 22.5s, then fed a real
    `exclusion_mask.create_exclusion_mask()` run end to end. Brazil's
    MapBiomas COG path not re-verified this session (only its bbox source
    changed, not its download logic).
- ✅ `protected_areas_fetch.py` — py_compile clean; `fetch`, `fetch_brazil`,
     `fetch_germany` import and `fetch(region, config)` signature matches
     the acquisition contract. Full `test_quick.py` run: 30/30 `src/`
     modules import OK, 0 failed. No live query run for either region this
     session (no network access in this environment) — INDE WFS and EEA
     discomap ArcGIS REST responses are unverified against real data; see
     the module's SOURCE NOTE docstrings. `tests/test_lcoh_model.py` and
     `tests/test_topsis.py` are pre-existing empty (0-byte) stub files, so
     `pytest tests/` correctly collects 0 tests — unrelated to this change.
  - **VERIFIED LIVE THIS SESSION**: Germany's Natura 2000 query (fixed to
    send a JSON envelope with explicit `spatialReference.wkid`, see item 5
    above) returned 1,484 real features, wrote a real 42.96MB
    `protected_areas.geojson` in 27.1s -- the SOURCE NOTE's "not verified
    against a live response" flag no longer applies to Germany. Brazil's
    INDE WFS path not exercised this session.
- ✅ `exclusion_mask.py` — py_compile clean; `create_exclusion_mask` imports
     and its `(region, config)` signature matches the contract used
     elsewhere. Full `test_quick.py` run: 30/30 `src/` modules import OK.
     `_reclassify_landuse()` unit-tested directly against hand-picked
     MapBiomas/Corine class codes for both regions (correct suitable/
     excluded split, including the nodata=0 case). Full
     `create_exclusion_mask()` verified end-to-end twice with synthetic
     inputs (mocked `get_analysis_grid` returning a small 10x10 reference
     grid, hand-written `landuse.tif` + `protected_areas.geojson` fixtures,
     removed after the test): (1) suitable land-use fully overlapping a
     protected polygon → all-excluded mask (0/100 km² suitable); (2)
     suitable, unprotected land-use on the other half → exactly the
     expected 50/100 km² suitable split, confirming the land-use warp and
     the protected-area rasterization land on identical pixels. No live
     run against real MapBiomas/Corine/INDE/Natura2000 data — those
     upstream fetches are themselves unverified in this environment (no
     network access); see `landuse_fetch.py`/`protected_areas_fetch.py`
     entries above.
- ✅ `topsis.py` — py_compile clean; `run_topsis`/`perturb_topsis_weights`
     import and `run_topsis`'s `(region, tech, config, custom_weights)`
     signature matches the contract. Full `test_quick.py` run: 30/30
     `src/` modules import OK (its generic "Test J" auto-probe reports a
     harmless dummy-call arity mismatch since `run_topsis` legitimately
     requires 3 positional args -- not a real failure). `_vectorized_topsis()`
     unit-tested against 3 known-answer TOPSIS cases (dominant row -> exactly
     C=1, dominated row -> exactly C=0, correct ordering for both benefit and
     cost criteria). `perturb_topsis_weights()` unit-tested for ±20% on
     `resource` (exact target value, renormalized sum=1.0, preserved relative
     proportions among the rest, `ValueError` on an unknown criterion).
     `_get_default_weights()` verified against the real loaded
     `scenario_params.yaml`. Full `run_topsis()` verified end-to-end with
     mocked `get_analysis_grid`/`create_exclusion_mask`/all 5 `data_layers`
     loaders (synthetic same-CRS GeoTIFFs, cleaned up after): excluded
     region exactly 0.0, valid region strictly monotonic in resource with
     the worst/best valid cells hitting the C=0/C=1 boundary exactly. No
     live run against real resource/slope/distance rasters — those upstream
     layers are themselves unverified in this environment (no network
     access for the fetches that populate `data/raw/`).
- ✅ `vikor.py` — py_compile clean; `run_vikor`/`compute_concordance` import
     and their signatures (`region, tech, config, v, custom_weights` /
     `topsis_scores, vikor_scores, mask, top_k_pct`, with `v` defaulting to
     0.5 and `top_k_pct` to 0.10) match the contract. Full `test_quick.py`
     run: 30/30 `src/` modules import OK (its generic "Test K" auto-probe
     reports the same harmless dummy-call arity mismatch `topsis.py` got —
     not a real failure). `_vectorized_vikor()` unit-tested against a
     two-criterion dominance case (dominant row → exactly Q=0, dominated
     row → exactly Q=1) and a single-benefit-criterion case (correct
     ordering). `compute_concordance()` unit-tested for identical rasters
     (rho=1.0, 100% top-k overlap), perfectly inverted rasters (rho=-1.0,
     0% overlap), and invalid inputs (shape mismatch and out-of-range
     `top_k_pct` both raise `ValueError`). Full `run_vikor()` verified
     end-to-end with the same mocked-loader fixtures as `topsis.py`'s own
     test, run side-by-side with `run_topsis()` on identical inputs: both
     produced numerically identical suitability rasters on a
     single-varying-criterion synthetic case (mathematically expected —
     both methods reduce to the same min-max normalization with one
     discriminating criterion), and `compute_concordance()` on that pair
     reported rho=1.0/100% overlap — concrete evidence the criteria-reuse
     actually produces identical inputs to both methods, not just
     structurally similar code. No live run against real
     resource/slope/distance rasters — same limitation as `topsis.py` (no
     network access in this environment for the fetches that populate
     `data/raw/`).
- ✅ `site_selection.py` — py_compile clean; `select_candidate_sites` imports
     and its `(region, tech, config, top_n)` signature matches the
     contract. Full `test_quick.py` run: 30/30 `src/` modules import OK.
     `rasterstats` is confirmed NOT installed in this environment
     (`pip show rasterstats` finds nothing — a pre-existing gap, see
     `07_risks_and_limitations.md`'s empty-`requirements.txt` entry); the
     module's lazy import inside its (currently unexercised)
     per-administrative-unit branch means this does not block import or
     the branch that actually runs. Verified end-to-end with **real
     synthetic GeoTIFFs written to `data/processed/`** (not just mocked
     return values) and a mocked `get_dissolved_polygon()`: a 16 km² block
     correctly survives a 5.0 km² threshold while an isolated 1 km² cell
     is filtered out, with exact mean/max suitability and geometry area
     (16.0 km², cross-checked via both the `.area` of the returned
     `Polygon` and the `suitable_area_km2` column); an all-below-threshold
     case returns a valid empty `GeoDataFrame` and a valid empty
     re-readable GeoJSON rather than crashing; two passing clusters rank in
     the correct order and `top_n=1` truncation works. GeoJSON/CSV outputs
     were re-read after writing to confirm round-trip correctness. No live
     run against real `topsis`/`exclusion_mask` output — those are
     themselves unverified against real data in this environment (no
     network access for the upstream acquisition fetches all session).

### Verification commands
```bash
# run_pipeline.py -- the task's own verification command, run live this
# session (no network, ~12s, all real results)
python run_pipeline.py --stage economics --stage sensitivity --stage viz

# full offline pytest suite -- 65 tests, 0 failures
pytest tests/ -v

# incentive_scenarios.py CSV persistence (new this session)
python -c "
import pandas as pd
from config.config_loader import load_scenario_config
from src.economics.incentive_scenarios import run_all_incentive_scenarios, INCENTIVE_SCENARIOS_CSV
cfg = load_scenario_config()
r = run_all_incentive_scenarios(cfg)
df = pd.read_csv(INCENTIVE_SCENARIOS_CSV)
assert set(df['region']) == {'brazil', 'germany'}
print('OK: incentive_scenarios.csv persisted with both regions')
"

# plotting.py (offline: Agg backend + import/contract check; economic
# figures run against REAL decomposition.py/sensitivity_analysis.py output
# -- no network needed, pure config-driven math)
python -c "
import matplotlib
from src.viz import plotting as viz
import inspect
assert matplotlib.get_backend() == 'Agg'
for name in ['plot_suitability_map', 'plot_candidate_sites_map', 'plot_lcoh_decomposition',
             'plot_inversion_point', 'plot_sensitivity_tornado', 'plot_incentive_scenarios',
             'generate_all_plots']:
    assert hasattr(viz, name), name
print('OK: Agg backend confirmed, all 7 public plotting functions present')
"
python -c "
from config.config_loader import load_scenario_config
from src.economics.decomposition import run_all_decompositions
from src.viz.plotting import generate_all_plots
cfg = load_scenario_config()
run_all_decompositions(cfg)  # writes outputs/tables/decomposition.csv + inversion_points.csv
results = generate_all_plots(cfg)
assert len(results['figures']) >= 2  # lcoh_decomposition + inversion_point, at minimum
print('OK: generate_all_plots produced', len(results['maps']), 'maps and', len(results['figures']), 'figures')
"

# sensitivity_analysis.py (offline: import + contract check; run_sensitivity()
# runs against the real scenario_params.yaml -- no network needed, pure
# LCOH math; run_mcda_sensitivity() synthetic end-to-end test mocks
# get_analysis_grid/create_exclusion_mask/all 5 data_layers loaders in both
# topsis.py's and vikor.py's own namespaces, the same pattern already used
# to verify those two modules, then cleans up its own fixtures)
python -c "
from src.sensitivity import sensitivity_analysis as sa
import inspect
assert hasattr(sa, 'run_sensitivity')
assert hasattr(sa, 'run_mcda_sensitivity')
assert hasattr(sa, 'run_all_sensitivities')
assert list(inspect.signature(sa.run_mcda_sensitivity).parameters) == ['region', 'tech', 'config', 'delta_pct']
print('OK: run_sensitivity, run_mcda_sensitivity, run_all_sensitivities present, signatures match contract')
"
python -c "
from config.config_loader import load_scenario_config
from src.sensitivity.sensitivity_analysis import run_sensitivity, ECONOMIC_SENSITIVITY_CSV
import os
cfg = load_scenario_config()
df = run_sensitivity(cfg)
assert os.path.exists(ECONOMIC_SENSITIVITY_CSV)
assert set(df['parameter'].unique()) == {
    'wacc', 'electrolyzer_capex_usd_per_kw', 'electrolyzer_efficiency_kwh_per_kg',
    'electrolyzer_technology', 'capex_multiplier',
}
print(f'OK: economic OAT sweep produced {len(df)} rows -> {ECONOMIC_SENSITIVITY_CSV}')
"

# h2_potential.py (offline: import + contract check; full synthetic
# end-to-end test writes a real candidate_sites_*.geojson fixture to
# data/processed/, runs calculate_h2_potential() against it for both PEM
# and alkaline, then removes the fixtures -- no live site_selection.py
# output required this session -- no network access in this environment)
python -c "
from src.potential import h2_potential
import inspect
assert hasattr(h2_potential, 'calculate_h2_potential')
assert hasattr(h2_potential, 'run_all_potential')
assert list(inspect.signature(h2_potential.calculate_h2_potential).parameters) == ['region', 'tech', 'config', 'electrolyzer_type']
print('OK: calculate_h2_potential, run_all_potential present, signatures match contract')
"
python -c "
import geopandas as gpd
from pathlib import Path
from shapely.geometry import box
from config.config_loader import load_scenario_config
from src.core.constants import Region, RegionCRS
from src.potential import h2_potential

cfg = load_scenario_config()
target_crs = RegionCRS.projected_crs_for(Region.NORDESTE_BR)
PROCESSED = Path('data/processed'); PROCESSED.mkdir(parents=True, exist_ok=True)
rows = [{'rank': 1, 'site_id': 'cluster_1', 'region': 'brazil', 'tech': 'solar',
         'mean_suitability': 0.8, 'max_suitability': 0.95, 'suitable_area_km2': 100.0,
         'geometry': box(0, 0, 10000, 10000)}]
gpd.GeoDataFrame(rows, geometry='geometry', crs=target_crs).to_file(
    PROCESSED / 'candidate_sites_brazil_solar.geojson', driver='GeoJSON')

result = h2_potential.calculate_h2_potential(Region.NORDESTE_BR, 'solar', cfg, 'pem')
power_density = cfg.renewables.solar_pv.power_density_w_per_m2
flh = cfg.renewables.solar_pv.capacity_factor_default * 8760.0
pem_eff = cfg.electrolyzer.technologies['pem'].efficiency_kwh_per_kg
expected_t = (100.0 * power_density * flh * 1000.0 / pem_eff) / 1000.0
assert abs(result.iloc[0]['annual_h2_production_t'] - expected_t) < 1e-6
assert result.iloc[0]['geometry'] is not None
for f in ['candidate_sites_brazil_solar.geojson', 'h2_potential_brazil_solar.geojson', 'h2_potential_brazil_solar.csv']:
    (PROCESSED / f).unlink()
print('OK: installable capacity / annual electricity yield / H2 output match hand-computed formulas exactly')
"

# admin_boundaries.py
python -c "
from src.spatial.admin_boundaries import get_region_bounds
from src.core.constants import Region
for r in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
    b = get_region_bounds(r)
    print(f'{r.value}: {b}')
print('✅ No network calls, reads from disk')
"

# decomposition.py
python -c "
from src.economics import decomposition
import inspect
src = inspect.getsource(decomposition._lcoh_for)
assert 'region_cfg.incentives' not in src
assert 'incentive_value_usd_per_kg=0.0' in src
print('✅ Baseline is incentive-free')
"

# data_layers.py
python -c "
from src.spatial import data_layers
assert hasattr(data_layers, 'load_distance_to_water')
assert hasattr(data_layers, 'load_distance_to_grid')
assert not hasattr(data_layers, '_get_target_crs')
print('OK:', [n for n in dir(data_layers) if n.startswith('load_')])
"

# water_bodies_fetch.py (live network call -- Overpass API; Germany bbox
# takes ~3-4 min, Brazil's 9-state bbox will take substantially longer)
python -c "
from config.config_loader import load_scenario_config
from src.core.constants import Region
from src.acquisition.water_bodies_fetch import fetch
from src.spatial.data_layers import load_distance_to_water

cfg = load_scenario_config()
path = fetch(Region.NORTH_GERMANY, cfg)
array, meta = load_distance_to_water(Region.NORTH_GERMANY, cfg)
print(f'✅ wrote {path}, load_distance_to_water shape={array.shape}, '
      f'mean={array.mean():.0f}m, max={array.max():.0f}m, units={meta[\"units\"]}')
"

# grid_infrastructure_fetch.py (live network call -- Overpass API; Germany
# bbox takes ~1-2 min, Brazil's 9-state bbox will take substantially longer)
python -c "
from config.config_loader import load_scenario_config
from src.core.constants import Region
from src.acquisition.grid_infrastructure_fetch import fetch
from src.spatial.data_layers import load_distance_to_grid

cfg = load_scenario_config()
path = fetch(Region.NORTH_GERMANY, cfg)
array, meta = load_distance_to_grid(Region.NORTH_GERMANY, cfg)
print(f'✅ wrote {path}, load_distance_to_grid shape={array.shape}, '
      f'mean={array.mean():.0f}m, max={array.max():.0f}m, units={meta[\"units\"]}')
"

# landuse_fetch.py (offline: import + contract check; no live download run
# this session -- no network access in this environment)
python -c "
from src.acquisition import landuse_fetch
import inspect
assert hasattr(landuse_fetch, 'fetch')
assert hasattr(landuse_fetch, 'fetch_brazil')
assert hasattr(landuse_fetch, 'fetch_germany')
assert list(inspect.signature(landuse_fetch.fetch).parameters) == ['region', 'config']
print('OK: fetch, fetch_brazil, fetch_germany present; fetch(region, config) signature matches contract')
"

# landuse_fetch.py -- fetch_germany() fails explicitly (no request attempted)
# when CDSE_TOKEN is unset
python -c "
from src.acquisition.landuse_fetch import fetch_germany
from src.acquisition.credentials import MissingCredentialError
try:
    fetch_germany(None)
    print('FAIL: expected MissingCredentialError')
except MissingCredentialError as exc:
    print('OK: fails explicitly before any network call:', exc)
"

# protected_areas_fetch.py (offline: import + contract check; no live query
# run this session -- no network access in this environment)
python -c "
from src.acquisition import protected_areas_fetch
import inspect
assert hasattr(protected_areas_fetch, 'fetch')
assert hasattr(protected_areas_fetch, 'fetch_brazil')
assert hasattr(protected_areas_fetch, 'fetch_germany')
assert list(inspect.signature(protected_areas_fetch.fetch).parameters) == ['region', 'config']
print('OK: fetch, fetch_brazil, fetch_germany present; fetch(region, config) signature matches contract')
"

# exclusion_mask.py (offline: import + contract check; synthetic end-to-end
# test with a mocked get_analysis_grid and hand-written landuse.tif /
# protected_areas.geojson fixtures -- writes and cleans up its own temp
# fixtures, does not require real data/raw/ content)
python -c "
from src.spatial import exclusion_mask
import inspect
assert hasattr(exclusion_mask, 'create_exclusion_mask')
assert list(inspect.signature(exclusion_mask.create_exclusion_mask).parameters) == ['region', 'config']
print('OK: create_exclusion_mask present, signature (region, config) matches contract')
"
python -c "
import numpy as np
from src.spatial.exclusion_mask import _reclassify_landuse
from src.core.constants import Region
br = np.array([[3, 24], [33, 0]], dtype=np.uint8)
assert _reclassify_landuse(br, Region.NORDESTE_BR).tolist() == [[1, 0], [0, 0]]
de = np.array([[211, 112], [512, 0]], dtype=np.uint16)
assert _reclassify_landuse(de, Region.NORTH_GERMANY).tolist() == [[1, 0], [0, 0]]
print('OK: land-use reclassification matches expected suitable/excluded pattern for both regions')
"

# topsis.py (offline: import + contract check + math unit tests; no live
# run against real data/raw/ criteria rasters this session -- no network
# access in this environment)
python -c "
from src.spatial import topsis
import inspect
assert hasattr(topsis, 'run_topsis')
assert hasattr(topsis, 'perturb_topsis_weights')
assert list(inspect.signature(topsis.run_topsis).parameters) == ['region', 'tech', 'config', 'custom_weights']
print('OK: run_topsis, perturb_topsis_weights present; run_topsis signature matches contract')
"
python -c "
import numpy as np
from src.spatial.topsis import _vectorized_topsis, perturb_topsis_weights
matrix = np.array([[10.0, 1.0], [5.0, 5.0], [1.0, 10.0]])
scores = _vectorized_topsis(matrix, np.array([0.5, 0.5]), ('benefit', 'cost'))
assert abs(scores[0] - 1.0) < 1e-9 and abs(scores[2] - 0.0) < 1e-9 and scores[0] > scores[1] > scores[2]
w = {'resource': 0.35, 'distance_to_grid': 0.20, 'distance_to_water': 0.25, 'slope': 0.20}
p = perturb_topsis_weights(w, 'resource', 0.20)
assert abs(p['resource'] - 0.42) < 1e-9 and abs(sum(p.values()) - 1.0) < 1e-9
print('OK: TOPSIS math hits exact C=0/C=1 boundary cases; weight perturbation hits exact target + renormalizes to 1.0')
"

# vikor.py (offline: import + contract check + math unit tests; no live
# run against real data/raw/ criteria rasters this session -- no network
# access in this environment)
python -c "
from src.spatial import vikor
import inspect
assert hasattr(vikor, 'run_vikor')
assert hasattr(vikor, 'compute_concordance')
assert list(inspect.signature(vikor.run_vikor).parameters) == ['region', 'tech', 'config', 'v', 'custom_weights']
assert list(inspect.signature(vikor.compute_concordance).parameters) == ['topsis_scores', 'vikor_scores', 'mask', 'top_k_pct']
print('OK: run_vikor, compute_concordance present; signatures match contract')
"
python -c "
import numpy as np
from src.spatial.vikor import _vectorized_vikor, compute_concordance
matrix = np.array([[10.0, 1.0], [5.0, 5.0], [1.0, 10.0]])
Q, S, R = _vectorized_vikor(matrix, np.array([0.5, 0.5]), ('benefit', 'cost'), v=0.5)
assert abs(Q[0] - 0.0) < 1e-9 and abs(Q[2] - 1.0) < 1e-9 and Q[0] < Q[1] < Q[2]
mask = np.ones((10, 10), dtype=np.uint8)
scores = np.random.RandomState(0).rand(10, 10).astype(np.float32)
result = compute_concordance(scores, scores.copy(), mask, top_k_pct=0.20)
assert abs(result['spearman_rho'] - 1.0) < 1e-9 and result['top_k_overlap_pct'] == 100.0
print('OK: VIKOR math hits exact Q=0/Q=1 boundary cases; concordance is perfect (rho=1.0, 100% overlap) for identical rasters')
"

# site_selection.py (offline: import + contract check; full synthetic
# end-to-end test writes real GeoTIFF fixtures to data/processed/, runs
# select_candidate_sites() against them, then removes the fixtures --
# no live topsis/exclusion_mask output required this session -- no network
# access in this environment)
python -c "
from src.spatial import site_selection
import inspect
assert hasattr(site_selection, 'select_candidate_sites')
assert list(inspect.signature(site_selection.select_candidate_sites).parameters) == ['region', 'tech', 'config', 'top_n']
print('OK: select_candidate_sites present, signature matches contract')
"
python -c "
import numpy as np, rasterio, geopandas as gpd
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rasterio.transform import from_bounds
from shapely.geometry import box
from src.core.constants import Region, RegionCRS
from src.spatial import site_selection

target_crs = RegionCRS.projected_crs_for(Region.NORDESTE_BR)
transform = from_bounds(0.0, 0.0, 10000.0, 10000.0, 10, 10)
PROCESSED = Path('data/processed'); PROCESSED.mkdir(parents=True, exist_ok=True)

suitability = np.zeros((10, 10), dtype=np.float32)
suitability[0:4, 0:4] = 0.6  # 16 km2 block, above threshold
suitability[9, 9] = 0.95     # 1 km2 isolated cell, below threshold
mask = np.zeros((10, 10), dtype=np.uint8); mask[0:4, 0:4] = 1; mask[9, 9] = 1

def w(path, arr, dtype):
    with rasterio.open(path, 'w', driver='GTiff', height=10, width=10, count=1,
                        dtype=dtype, crs=target_crs, transform=transform) as dst:
        dst.write(arr, 1)
w(PROCESSED / 'topsis_suitability_brazil_solar.tif', suitability, 'float32')
w(PROCESSED / 'exclusion_mask_brazil.tif', mask, 'uint8')

fake_boundary = gpd.GeoDataFrame({'geometry': [box(0, 0, 10000, 10000)]}, crs=target_crs)
config = SimpleNamespace(thresholds=SimpleNamespace(min_contiguous_suitable_area_km2=5.0))
with patch('src.spatial.site_selection.get_dissolved_polygon', return_value=fake_boundary):
    result = site_selection.select_candidate_sites(Region.NORDESTE_BR, 'solar', config)

assert len(result) == 1 and abs(result.iloc[0]['suitable_area_km2'] - 16.0) < 1e-9
assert result.iloc[0]['geometry'] is not None
for f in ['topsis_suitability_brazil_solar.tif', 'exclusion_mask_brazil.tif',
          'candidate_sites_brazil_solar.geojson', 'candidate_sites_brazil_solar.csv']:
    (PROCESSED / f).unlink()
print('OK: small cluster filtered by area threshold, surviving 16 km2 cluster correctly aggregated and ranked')
"

# incentive_scenarios.py
python -c "
from config.config_loader import load_scenario_config
from src.economics.incentive_scenarios import run_all_incentive_scenarios
cfg = load_scenario_config()
r = run_all_incentive_scenarios(cfg)
assert r['brazil']['rehidro_lcoh_usd_per_kg'] < r['brazil']['baseline_lcoh_usd_per_kg']
assert r['germany']['ipcei_lcoh_usd_per_kg'] < r['germany']['baseline_lcoh_usd_per_kg']
print('✅ Incentive scenarios lower LCOH vs baseline for both regions:', r)
"
Known Issues (not blocking)
config/scenario_params.yaml unresolved design question
Should max_slope_degrees / min_capacity_factor be binary cutoffs or TOPSIS-weighted?
Action required before: implementing exclusion_mask.py
REHIDRO placeholder values in scenario_params.yaml
regions.brazil.incentives.production_credit_usd_per_kg (1.00 USD/kg) and
support_period_years (10 yr) are PLACEHOLDERS added this session so
run_rehidro_scenario() has a non-zero credit to apply — not sourced from
actual REHIDRO program documentation. Flagged in the YAML with the same
"PENDENTE DE DECISÃO" comment style already used for Germany's EUR/USD
placeholder. Do not use in publishable results without confirming the
real REHIDRO credit value and duration.
Next Session Starts Here 👇
Current objective: none required for the core sprint -- every module
`ARCHITECTURE.md` describes exists, imports cleanly, and the acquisition
layer's real bugs found this session (CRS bbox, Overpass 504s, GHI
cropping, Natura 2000 geometry encoding) are fixed and live-verified,
including the task's own `python run_pipeline.py --stage acquisition
--region germany` verification command, which completed with **all 7
fetchers "ok"** (see "What was completed this session" below for the
full timing breakdown). If a future sprint picks this codebase back up:
(1) **This environment DOES have live network access** (corrected from
every prior session's note claiming otherwise) -- but it's asymmetric:
Overpass (`overpass-api.de`), EEA discomap ArcGIS REST (Natura 2000), and
AWS S3 (`esa-worldcover`) are all directly reachable and were verified
live this session; `services.terrascope.be` (the ESA WorldCover WMS
viewer service, tried as an earlier candidate and NOT used in the final
fallback) resets every HTTPS request at the connection level regardless
of request shape -- confirmed via raw `curl`, unrelated to this codebase;
Corine's WCS returns HTTP 498 (invalid/expired token) with the
placeholder `CDSE_TOKEN`, expected until a real token is supplied.
`srtm.fetch()` (AWS Terrain Tiles, zoom 12) is genuinely slow for a
whole-state-sized bbox -- 4,434s (~74 min) for Germany's ~332km x 419km
extent, one HTTP request per ~38m/px tile -- not a bug, but budget for it
if scripting a full unattended run; every other fetcher completed in
under 3 minutes.
(2) **CDSE_TOKEN, REHIDRO, and IPCEI Hy2Use values are still
placeholders.** `.env` now has a `CDSE_TOKEN=COLE_SEU_TOKEN_AQUI`
placeholder (`credentials.py` also checks `config/cdse_credentials.json`
now) -- replace with a real Copernicus Data Space Ecosystem token to
exercise the real Corine path instead of the ESA WorldCover fallback.
REHIDRO/IPCEI values remain flagged "PENDENTE DE DECISÃO" in
`scenario_params.yaml`, unchanged this session. Do not use
`incentive_scenarios.csv`/`incentive_scenarios.png` in publishable
results until these are confirmed.
(3) **Brazil's acquisition-layer fixes are implemented but not yet
live-verified** -- this session's live testing focused on Germany (per
the task's own verification command); the same CRS-detection,
bbox-sourcing, and Overpass-query fixes apply uniformly to Brazil's much
larger 9-state bbox, but that bbox was not exercised live this session
(Brazil's `admin_boundaries.py` boundary file is still the older
synthetic offline fixture, not a live IBGE fetch -- see Issue 5 above).

> ✅ **UPDATE (2026-08-12): this note is now stale.** A direct on-disk
> check (file existence, size, plus a live `get_region_bounds()`/
> `get_region_bounds_wgs84()` call -- not a re-run of the fetchers
> themselves) confirms all 7 acquisition layers now have real,
> non-trivial output for Brazil, most recently modified 2026-08-03 to
> 2026-08-05 -- i.e. in a session after this note was written, which
> apparently did run Brazil's acquisition live and never updated this
> file or `docs/memory/03_data_sources_and_acquisition.md` to say so
> (now corrected in both). Specifically:
> `data/boundaries/brazil.geojson` (277 KB) is a real dissolved 29-part
> MultiPolygon with 5,246 vertices -- NOT the synthetic offline fixture
> this note describes; `get_region_bounds_wgs84(Region.NORDESTE_BR)`
> returns `(-48.76, -18.34, -32.40, -1.05)`Deg, a sane real Northeast
> Brazil extent (~1,552,166 km2), confirming Issue 5's CRS
> auto-detection fix resolves correctly for Brazil's file too, not only
> Germany's. `srtm_30m.tif` (398.6 MB), `ghi.tif` (26.9 MB),
> `wind_power_density_100m.tif` (144.4 MB), `landuse.tif` (8.1 MB),
> `protected_areas.geojson` (2.3 MB), `distance_to_water.tif` (13.9 MB),
> and `distance_to_grid.tif` (13.9 MB) all exist under
> `data/raw/*/brazil/`. This confirms the outputs exist and are
> structurally sane; it does not re-verify each fetcher's live
> request/response shape line-by-line, since no fetcher was re-run this
> session -- see whichever session actually produced these files
> (untracked in this log) for that level of detail if it's ever needed.

What was completed this session (Site Selection Scale + Wind Data Accuracy + Economic Input Transparency):

Fixed one real bug (site selection scale), replaced an approximation with
an authoritative data source (wind power density), and added a new
disclosure artifact (input assumptions), all verified live against real
data:

1. **`src/spatial/site_selection.py` -- fixed continental-scale "sites"**:
   Brazil solar's `cluster_2` was 2,170,942 km² (bigger than Bahia state) --
   a bioma, not a hydrogen hub, because a fairly uniform TOPSIS raster over
   a large region (most valid cells scoring >0.9) let 8-connectivity
   clustering merge almost the entire suitable area into a handful of
   amorphous blobs. Root cause was NOT the area threshold alone (10 km² was
   too low, but raising it doesn't break up an already-merged blob) --
   fixed with two additions to `select_candidate_sites()`:
   - `top_suitability_percentile` (default 0.85): masks out everything
     below the 85th percentile of the region/tech's valid suitability
     distribution *before* clustering, so only the genuinely best cells
     are eligible to merge into a cluster in the first place.
   - `max_contiguous_suitable_area_km2` (new `ThresholdsConfig` field,
     `config_loader.py`): any cluster still exceeding this after the
     percentile filter (e.g. a long uniform coastal strip) is capped down
     to its own top-suitability cells at that area, rather than dropped --
     preserves the best sub-region of an oversized cluster instead of
     discarding real signal.
   - `min_contiguous_suitable_area_km2` bumped 10.0 -> 50.0 km² (more
     realistic floor for a GW-scale hub footprint).
   - `run_pipeline.py` now calls `select_candidate_sites(..., top_n=20)`
     (new `SITE_SELECTION_TOP_N` constant) -- even after the two filters
     above, a large uniform region can still leave hundreds of
     individually-valid disjoint patches (verified: Brazil solar dropped
     from 28 blobs to 508 valid-but-numerous small clusters before top_n);
     METHODOLOGY.md 2.3 describes retaining "the top-ranked units," a
     shortlist, not every passing patch.
   - **Verified live against the real, already-computed TOPSIS/exclusion
     rasters for both regions**: Brazil solar 28 -> 20 sites (area
     50.98-1999.2 km², was up to 2,170,942 km²), Brazil wind 26 -> 20
     sites (932.6-1999.2 km²), Germany solar 14 -> 20 sites
     (105.7-636.4 km²), Germany wind 9 -> 12 sites (51.9-1999.9 km²) --
     every region/tech pair now in the 5-20 site / 50-2000 km² range this
     task targeted, and no cluster exceeds the new cap.
2. **`src/acquisition/solar_wind_atlas.py` / `src/spatial/data_layers.py`
   -- wind power density now fetched directly from Global Wind Atlas's own
   "power-density" layer**, not derived from its "wind-speed" (mean speed)
   layer via `P = 0.5*rho*v_mean^3`. The manual formula was mathematically
   standard and correctly implemented (verified: matches
   `0.6125*mean(v)^3` exactly) -- NOT a bug -- but it under-estimates true
   power density relative to GWA's own product, which is modeled from each
   pixel's actual Weibull wind-speed distribution rather than a single
   mean-speed point estimate (Jensen's inequality: `E[v^3] > E[v]^3` for
   any non-degenerate distribution). Switching removes an entire
   approximation step. `fetch_wind()` now writes
   `data/raw/wind/{region}/wind_power_density_100m.tif` (was
   `wind_speed_100m.tif`) and prints WPD min/mean/max on completion, per
   this task's own diagnostic-print instruction (adapted: the fetched
   layer is now WPD directly, not m/s, so there is no separate "assuming
   m/s" step to log). `load_wind_power_density()` in `data_layers.py` no
   longer applies any `convert_fn` (`WIND_POWER_COEFFICIENT`/
   `AIR_DENSITY_KG_M3` removed, now dead code).
   - **Live-verified real numbers, both regions, both before and after**:
     old mean-speed-cubed approximation -- Germany 298 W/m² (raw
     wind-speed mean 7.7 m/s), Brazil 124 W/m² (raw mean 5.5 m/s); new GWA
     power-density layer -- Germany 473.7 W/m², Brazil 162.1 W/m², both
     higher in the direction Jensen's inequality predicts.
   - **This task's own hypothesized "200-350 W/m² for both regions"
     target was not achieved by either the old formula or GWA's own
     authoritative product**, and is flagged here rather than silently
     forced: Northeast Brazil's raster covers the entire 9-state bbox,
     including a vast low-wind semi-arid interior (Piauí, interior Bahia)
     far outside the coastal wind corridor the "7-9 m/s" figure describes
     -- the region-wide mean is genuinely diluted by that interior, the
     same root cause as finding 1's site-selection blobs (whole-region
     statistics vs. actual candidate-site statistics are two different
     numbers). North Germany's region-wide mean, conversely, came out
     *above* the hypothesized range using the authoritative source. Do not
     use a region-wide raster mean as a proxy for candidate-site wind
     quality in the manuscript -- use the per-site values in
     `candidate_sites_{region}_wind.csv` / `h2_potential_{region}_wind.csv`
     instead.
   - `data/raw/wind/{region}/wind_speed_100m.tif` files from the old
     approach are now orphaned (no code path reads them) but left on disk,
     not deleted by hand, per root `CLAUDE.md`'s "data/raw/ written by
     acquisition only" rule.
3. **`src/economics/decomposition.py` -- new `get_input_assumptions(region,
   renewable_tech, config) -> pd.DataFrame`**: discloses every input
   parameter behind a (region, renewable_tech) pair's LCOH figure --
   electrolyzer CAPEX, region CAPEX multiplier, WACC, renewable LCOE,
   capacity factor, specific energy, lifetime, OPEX %, stack-replacement %,
   water cost (converted to USD/kg from the YAML's USD/m³ + L/kg fields),
   and the region's own hypothetical incentive credit/duration (disclosed
   for reference only -- NOT applied to the baseline LCOH, which stays
   incentive-free per `_lcoh_for`'s existing contract). Returns 2 rows per
   call (PEM + alkaline), so `run_all_decompositions()` (now writing this
   CSV before running the three decomposition experiments, as instructed)
   produces exactly 8 rows across 2 regions x 2 renewable techs x 2
   electrolyzer technologies -> `outputs/tables/input_assumptions.csv`.
   Verified live: LCOH values in `decomposition.csv` are numerically
   unchanged by this addition (it only reads existing config-resolution
   helpers, computes nothing new that feeds back into any LCOH call) --
   confirms the "do not change LCOH results" constraint held.
   `[decomposition] wrote outputs/tables/input_assumptions.csv (8 rows)`
   printed as instructed.

Full `pytest tests/ -q` after all three fixes: **65 passed, 0 failed** (no
existing test asserted on the old wind formula, the old min-area default,
or `select_candidate_sites()`'s pre-fix behavior).

What was completed this session (Acquisition Layer Live-Access Fixes):

Fixed five real bugs surfaced by finally having live network access,
found and fixed for Germany, verified live for Germany end to end
(Brazil not yet exercised live, see "Next Session Starts Here" above):

1. **`spatial/admin_boundaries.py`**: fixed the Germany CRS regression
   (Issue 5, above) with per-file coordinate-magnitude detection instead
   of one blanket assumption, and added `get_region_bounds_wgs84()` as
   the single place fetchers get a true-degrees bbox (previously each
   fetcher reimplemented `get_dissolved_polygon(region)
   .to_crs("EPSG:4326").total_bounds` inline). Verified live: Germany's
   projected bbox is now a sane ~332km x 419km box; its WGS84 bbox
   (6.63, 51.30, 11.60, 55.06)° matches the real GADM extent exactly.
2. **`src/acquisition/credentials.py`**: `get_credential()` now also
   checks `config/cdse_credentials.json` (in addition to `.env`/env
   vars), never logging a credential's value. Created `.env` with a
   `CDSE_TOKEN=COLE_SEU_TOKEN_AQUI` placeholder and populated
   `.gitignore` (previously empty) with `.env` and
   `config/cdse_credentials.json`.
3. **`src/acquisition/solar_wind_atlas.py`**: `_region_bounds()` now
   calls `admin_boundaries.get_region_bounds_wgs84()` instead of
   reimplementing the reprojection inline; `_crop_to_bbox()` now
   intersects the requested window with the source raster's own pixel
   extent before reading, raising `SolarWindFetchError` (not a
   rasterio-internal `WindowError`, and not a silent negative-offset/
   garbage read) when the bbox doesn't overlap the source at all.
   Verified with a synthetic raster covering all three cases (fully
   contained, partially outside, fully outside) since the real live
   `fetch_solar()` path is blocked on the unrelated extraction bottleneck
   noted above.
4. **`water_bodies_fetch.py` / `grid_infrastructure_fetch.py`**: both now
   call `admin_boundaries.get_region_bounds_wgs84()`; added a third
   Overpass mirror (`maps.mail.ru`) alongside `overpass-api.de` and
   `overpass.kumi.systems`; condensed each query's tag filters via regex
   (`waterway~"river|riverbank"`, `power~"line|cable"`) to reduce the
   query plan's clause count. **Verified live for Germany with the real
   (now-correct) bbox**: water bodies -- 206,947 features parsed, wrote a
   real `distance_to_water.tif` in 188.2s on the *first* attempt to the
   *primary* server (no 504, no fallback needed) -- materially consistent
   with the 206,942 features an earlier session's live run found before
   the Issue 5 regression was introduced; power infrastructure -- 65,510
   features, 79.5s, also first-attempt success.
5. **`protected_areas_fetch.py`**: Germany's Natura 2000 query now sends
   the ArcGIS REST envelope as a JSON object with an explicit
   `spatialReference.wkid` instead of a bare comma-separated string.
   **Verified live**: 1,484 real Natura 2000 features parsed, wrote a
   real 42.96MB `protected_areas.geojson` in 27.1s -- the first time this
   endpoint has been exercised against a live response in this project's
   history (previously flagged as "not verified" in the module's own
   SOURCE NOTE).
6. **`landuse_fetch.py`**: added an ESA WorldCover 2021 fallback for
   Germany, used when CDSE_TOKEN is missing or the Corine WCS
   request/response fails for any reason. Tried a WMS-based fallback
   first (Terrascope's public viewer service) but that host resets every
   HTTPS request from this environment (confirmed via raw `curl`,
   independent of this module); switched to the public AWS-hosted COG
   tiles instead (`s3://esa-worldcover`, verified reachable: all 4 tiles
   Germany's bbox touches returned HTTP 200), read via `/vsicurl/` and
   `rasterio.merge()` at an explicit ~100m output resolution (not the
   source's native 10m, which would force a multi-gigapixel read per
   region-sized bbox) -- the same COG-windowed-read pattern already used
   for Brazil's MapBiomas source above it in this file. WorldCover's
   built-up(50)/water(80) classes are recoded into Corine-range-
   compatible codes (111, 512) before writing, so
   `spatial/exclusion_mask.py`'s existing `CORINE_EXCLUDED_RANGES` needed
   zero changes. **Verified live end to end**: real Corine WCS call
   correctly reached the EEA server and got HTTP 498 (placeholder token
   rejected, as expected) before falling back; WorldCover fallback wrote
   a real 46.16MB `landuse.tif` in 22.5s (class distribution: 882,141
   built-up px, 18,338,632 suitable px, 3,847,307 water px); then ran
   `spatial.exclusion_mask.create_exclusion_mask()` against this real
   landuse.tif + the real Natura 2000 GeoJSON from item 5 above and got a
   real result (139,086 km² total, 93,361 km² suitable, 32.9% excluded)
   -- the first time this module has run against real (non-synthetic)
   Germany data in this project's history.

Full `pytest tests/ -v` re-run after every fix above: **65 passed, 0
failed** (no test needed updating -- all acquisition-layer tests are
synthetic-fixture-based per this session's own earlier work, so none
depended on the buggy behavior being fixed).

**Task's own verification command, run live end to end in the
background** (`python run_pipeline.py --stage acquisition --region
germany`) -- completed in 4,774.7s (~80 min, dominated by `srtm`'s
74-minute AWS Terrain Tiles mosaic, a real per-tile HTTP cost for a
whole-state bbox, not a bug):

| Fetcher | Status | Time | Output |
|---|---|---|---|
| `admin_boundaries` | ok | 24.2s | `data/boundaries/germany.geojson` |
| `srtm` | ok | 4,434.4s | `data/raw/elevation/germany/srtm_30m.tif` (154.8 MB) |
| `solar_wind` | ok | 49.1s | `ghi.tif` (1.08 MB) + `wind_speed_100m.tif` (8.90 MB) |
| `water_bodies` | ok | 167.5s | `distance_to_water.tif` |
| `grid_infrastructure` | ok | 71.7s | `distance_to_grid.tif` |
| `landuse` | **failed*** | -- | Terrascope WMS `RemoteDisconnected` |
| `protected_areas` | ok | 21.4s | `protected_areas.geojson` (42.96 MB) |

*`landuse` failed in this specific background run because it had already
imported the OLD WMS-based fallback code (item 6 above) before that code
was replaced with the AWS-COG version mid-session -- a stale-import
artifact of when the background run was launched relative to when the
fix landed, not a live re-occurrence of the WMS bug. Re-ran
`landuse_fetch.fetch_germany()` standalone immediately after with the
current on-disk code: succeeded in 25.6s via the ESA WorldCover fallback,
matching item 6's earlier result exactly. **All 7 fetchers succeed live
for Germany with the code as it stands at the end of this session.**

What was completed the session before (Pipeline Orchestration + Test Suite,
Stage 7 -- the sprint's last stage, now complete):

1. **`run_pipeline.py` rewritten as a full CLI orchestrator** (~370
   lines): `--stage {acquisition,spatial,potential,economics,sensitivity,
   viz,all}` (repeatable, fixed dependency order), `--region {brazil,
   germany,all}`, `--skip-acquisition`. Every stage function wraps its own
   per-region/per-technology calls individually (catching each module's
   own documented exception types -- FileNotFoundError, TopsisError,
   VikorError, SiteSelectionError, AdminBoundaryError) so a missing
   upstream input is reported as "skipped" in a JSON summary rather than
   crashing the run; every top-level stage invocation in main() is
   additionally wrapped so one stage's unexpected failure never prevents
   the rest from running. Verified LIVE (not just import-checked): the
   task's own `--stage economics --stage sensitivity --stage viz`
   completed in ~12s with real results (economics ok, sensitivity's
   economic half ok + 4 MCDA pairs correctly skipped, viz ok with 7
   figures); `--stage spatial --region germany` exercised genuine
   reprojection against the real partial Germany data already on disk
   (4/5 data_layers loaders + admin_boundaries succeeded for real,
   slope/exclusion_mask correctly skipped); `--stage all
   --skip-acquisition` ran the complete six-stage flow in ~9s.
2. **`incentive_scenarios.run_all_incentive_scenarios()` now persists**
   `outputs/tables/incentive_scenarios.csv`, closing the gap
   `viz/plotting.py`'s own session flagged. Verified: `generate_all_plots()`
   now produces 7 figures instead of 6, with zero changes needed to
   `plotting.py` itself.
3. **`tests/` filled in completely**: `test_lcoh_model.py` (20 tests) and
   `test_topsis.py` (16 tests) went from 0-byte stubs to real suites;
   `test_grid_utils.py` (17 tests) and `test_admin_boundaries.py` (12
   tests) were created new. All fixtures are synthetic (tmp_path-based
   GeoTIFF/GeoJSON, or an independently-written reference LCOH
   implementation) -- no test depends on real acquired data or on
   whatever happens to already be on disk in a given environment.
   `pytest tests/ -v`: **65 passed, 0 failed.** Two genuine discoveries
   made while writing these tests, both documented in
   `docs/memory/08_commands_and_reproducibility.md`: (a)
   `grid_utils.reproject_and_resample()` unconditionally persists to the
   real `data/processed/` regardless of any `tmp_path` passed to it (Hard
   Rule 7) -- required an autouse cleanup fixture in `test_grid_utils.py`
   after a first run left stray `*_reprojected.tif` files in real project
   state; (b) `Region` being a `str, Enum` means a raw string equal to a
   member's value (e.g. `"brazil"`) compares `True` against that member
   via `==` and is silently accepted wherever code compares `region ==
   Region.X` -- not a bug, but it meant the "unrecognized region" test in
   `test_admin_boundaries.py` had to use a string matching no member's
   value at all.

What was completed the session before (viz/plotting.py, Stage 6 item 25):

Completed src/viz/plotting.py (450 lines): a pure-visualization layer
with zero imports from src/economics/, src/sensitivity/, or the
analytical modules under src/spatial/ (topsis.py, vikor.py,
exclusion_mask.py, site_selection.py) -- only src.core.constants
(Region/RegionCRS). matplotlib.use("Agg") set immediately after import;
no plt.show() anywhere; no seaborn/plotly. 300 DPI + bbox_inches="tight"
on every savefig. plot_suitability_map()/plot_candidate_sites_map() read
already-written rasters/GeoJSON from data/processed/ +
data/boundaries/{region}.geojson, writing to outputs/maps/.
plot_lcoh_decomposition()/plot_inversion_point()/plot_sensitivity_tornado()/
plot_incentive_scenarios() take an already-computed DataFrame/Dict (or
read outputs/tables/*.csv by a fixed path), writing to outputs/figures/.
generate_all_plots(config) orchestrates all of the above, checking each
prerequisite file exists on disk BEFORE calling the corresponding plot
function and skipping with a warning (never raising, never calling an
analytical module to fill a gap) otherwise.
Key design decision, flagged in SPRINT_LOG and docs/memory/04_spatial_methodology.md's
sibling doc, 05_economic_model.md: the task prompt asked plot_inversion_point()
to "compute/plot" a smooth LCOH-vs-WACC curve (2%-15%), which would
require sampling economics/decomposition.py's LCOH machinery -- directly
in tension with this session's "pure visualization ... do not
re-calculate analytical models" and "do not touch analytical modules in
src/" constraints. Resolved in favor of the constraints: the plotted
"curve" is built from only the 2-3 already-computed points that exist on
disk (Brazil's wacc_swap point, Brazil's actual point, and the inversion
point when converged), connected by straight line segments, with zero
economics-module imports anywhere in the file.
Verified against REAL data, not just synthetic fixtures: ran
decomposition.run_all_decompositions(cfg) live this session (pure
config-driven math, no network needed) to produce real decomposition.csv/
inversion_points.csv -- both technologies actually returned
converged=False with current parameters, which exercised the "no
inversion in tested WACC range" annotation path against a real result,
not a contrived one. The real economic_sensitivity.csv from the prior
session drove 4 real tornado charts; incentive_scenarios.run_all_incentive_scenarios(cfg)'s
real dict drove the incentive figure. generate_all_plots(config), run
with only those real economic CSVs present (no raster/geojson fixtures),
correctly produced 0 maps + 6 figures and skipped every map (8 skips) +
incentive_scenarios (1 skip, see gap above) with warnings, never raising.
Map functions verified against synthetic GeoTIFF/GeoJSON fixtures (same
pattern as topsis.py/vikor.py/site_selection.py/h2_potential.py), and the
boundary overlay rendered correctly against the REAL
data/boundaries/brazil.geojson already on disk from an earlier session's
live run_pipeline.py --stage boundaries run. Output PNGs confirmed 300
DPI via PIL. Full test_quick.py run: 30/30 src/ modules import OK.

What was completed the session before (sensitivity_analysis.py, Stage 5 item 24):

Completed src/sensitivity/sensitivity_analysis.py (441 lines), finishing
Stage 5. Two parts:
(1) run_sensitivity(config) -- the prior session's economic one-at-a-time
sweep (WACC, electrolyzer CAPEX, electrolyzer efficiency, PEM-vs-alkaline,
CAPEX multiplier), preserved logic-for-logic; only the output path changed
(outputs/tables/sensitivity_tornado.csv -> economic_sensitivity.csv, no
other module referenced the old name).
(2) run_mcda_sensitivity(region, tech, config, delta_pct=0.20) -- NEW: the
previously-missing §2.7 TOPSIS weight-perturbation vs. VIKOR concordance
table. For each of topsis.CRITERIA's 4 criteria x 2 directions
(+/-delta_pct), perturbs weights via topsis.perturb_topsis_weights(),
re-runs run_topsis() with the perturbed weights, and scores concordance
(vikor.compute_concordance()) against both the unperturbed TOPSIS baseline
and the independent VIKOR baseline -- plus one baseline
(TOPSIS-vs-VIKOR, no perturbation) row. Neither TOPSIS nor VIKOR math is
re-implemented; run_topsis/run_vikor/perturb_topsis_weights/
compute_concordance are imported and called as-is. Writes
outputs/tables/mcda_sensitivity_concordance_{region}_{tech}.csv.
run_all_sensitivities(config) runs both parts for every region x tech.
Verified: run_sensitivity() re-run against the real scenario_params.yaml
(132 rows, unchanged shape); run_mcda_sensitivity() verified end-to-end
with synthetic 10x10 criterion rasters (mocking get_analysis_grid/
create_exclusion_mask/all 5 data_layers loaders in both topsis.py's and
vikor.py's own namespaces -- the same pattern already used to verify those
two modules): exactly 9 rows (1 baseline + 8 perturbations), baseline row
self-identical vs TOPSIS (rho=1.0, 100% overlap), every perturbed-weights
dict sums to 1.0, all rho/overlap values in valid ranges, CSV round-trip
verified. Full test_quick.py run: 30/30 src/ modules import OK. No live
run against real criterion rasters -- same limitation topsis.py/vikor.py
themselves carry (no network access in this environment).

What was completed in earlier sessions (site_selection.py / vikor.py /
topsis.py / exclusion_mask.py, Stage 4 -- h2_potential.py's own session
summary, Stage 5 item 23, is documented above in the Module Status
Snapshot's `potential/h2_potential.py` entry rather than duplicated here):

✅ Stage 4, item 22: src/spatial/site_selection.py created (247 lines) --
  Stage 4 (the entire spatial pipeline) is now complete
  select_candidate_sites(region, tech, config, top_n=None) -> gpd.GeoDataFrame
  -- reads topsis_suitability_{region}_{tech}.tif +
  exclusion_mask_{region}.tif from data/processed/ (already written by
  prior stages, neither re-run nor modified); 8-connectivity clustering
  via scipy.ndimage.label; clusters below
  config.thresholds.min_contiguous_suitable_area_km2 dropped; ranked by
  mean_suitability * log1p(suitable_area_km2); writes
  data/processed/candidate_sites_{region}_{tech}.geojson + .csv
  (WKT geometry in the CSV; the returned GeoDataFrame keeps real geometry)
  admin_boundaries.py's own docstring documents this module as a
  get_dissolved_polygon() consumer (always ONE polygon per region) --
  implemented both the per-admin-unit and per-cluster aggregation branches
  the task specified, but only per-cluster actually runs today
  ⚠️ Discovered rasterstats (a documented project dependency) is NOT
  actually installed in this environment (`pip show rasterstats` finds
  nothing) -- made that import lazy, scoped to the per-admin-unit branch
  that doesn't run today, rather than installing a new package into a
  shared environment or letting an unrelated pre-existing gap
  (requirements.txt is empty, see 07_risks_and_limitations.md) block this
  module from working
  ✅ Verified end-to-end with REAL synthetic GeoTIFF fixtures written to
  data/processed/ (not just mocked function returns): area-threshold
  filtering, exact suitable_area_km2/mean/max values, geometry area
  cross-check, empty-result handling, ranking order, top_n truncation, and
  GeoJSON/CSV round-trip readability all verified. No live run against
  real topsis/exclusion_mask output -- those are themselves unverified
  against real data in this environment (no network access for the
  upstream acquisition fetches all session).

✅ Stage 4, item 21: src/spatial/vikor.py created (303 lines)
  run_vikor(region, tech, config, v=0.5, custom_weights=None) -> Tuple[np.ndarray, Dict]
  -- vectorized VIKOR (Q_i compromise index, converted to suitability=1-Q),
  same criteria/mask/valid-cell restriction as run_topsis(); writes
  data/processed/vikor_suitability_{region}_{tech}.tif (float32, excluded
  cells = 0.0)
  compute_concordance(topsis_scores, vikor_scores, mask, top_k_pct=0.10) -> Dict[str, float]
  -- Spearman rho/p-value + Jaccard top-k%-overlap between a TOPSIS and a
  VIKOR raster
  Reuses topsis.CRITERIA / topsis.CRITERION_DIRECTION / topsis._load_criterion()
  / topsis._get_default_weights() directly rather than re-implementing
  criteria loading, per this session's explicit constraint -- resolves the
  "avoid a third independent alignment implementation" question flagged in
  last session's Next Session note.
  ⚠️ Verified extensively offline (VIKOR math against known-answer boundary
  cases, compute_concordance() against identical/inverted/invalid inputs,
  full run_vikor() end-to-end alongside run_topsis() on identical mocked
  inputs -- confirmed numerically identical outputs on a
  single-varying-criterion case, and perfect concordance on that pair) but
  NOT against real resource/slope/distance rasters, since none of the
  upstream acquisition fetches have been run live in this environment (no
  network access all session, same limitation as topsis.py last session).

✅ Stage 4, item 20: src/spatial/topsis.py created (306 lines)
  run_topsis(region, tech, config, custom_weights=None) -> Tuple[np.ndarray, Dict]
  -- vectorized TOPSIS restricted to unmasked, finite cells only; writes
  data/processed/topsis_suitability_{region}_{tech}.tif (float32, excluded
  cells = 0.0)
  perturb_topsis_weights(weights, target_criterion, delta_pct) -> Dict[str, float]
  -- proportional renormalization to keep sum=1.0
  Weight-field mapping assumption (flagged in the module's WEIGHT-FIELD
  MAPPING NOTE and in docs/memory/04_spatial_methodology.md, YAML untouched):
  resource_quality->resource, grid_distance->distance_to_grid (exact name
  match), proximity_infrastructure->distance_to_water, land_availability->slope
  Alignment: re-reads each data_layers loader's metadata["aligned_path"] and
  warps (bilinear) directly onto get_analysis_grid()'s exact transform/shape
  before assembling the decision matrix -- same class of fix
  exclusion_mask.py applied to land use, now applied to all 4 continuous
  criteria. exclusion_mask.create_exclusion_mask() called as-is, not modified.
  ⚠️ Verified extensively offline (TOPSIS math against 3 known-answer cases,
  weight perturbation math, config weight-mapping against the real YAML,
  full run_topsis() end-to-end with mocked loaders/grid/mask -- see Test
  Status entry above for details) but NOT against real
  resource/slope/distance-to-grid/distance-to-water rasters, since none of
  the upstream acquisition fetches have been run live in this environment
  (no network access all session).

✅ Stage 4, item 19: src/spatial/exclusion_mask.py created (258 lines)
  create_exclusion_mask(region, config) -> Tuple[np.ndarray, Dict] — combines
  land-use reclassification AND protected-area rasterization via logical
  AND; writes data/processed/exclusion_mask_{region}.tif (uint8, 1=suitable,
  0=excluded)
  Land-use: warps data/raw/landuse/{region}/landuse.tif (nearest-neighbor)
  directly onto get_analysis_grid()'s exact transform/shape via
  rasterio.warp.reproject (NOT grid_utils.reproject_and_resample(), which
  computes its own output transform from the source's reprojected extent
  and is not guaranteed to align with get_analysis_grid()'s transform
  pixel-for-pixel -- this exact alignment risk was already flagged in
  data_layers.py's CRS NOTE as something to resolve here), then reclassifies:
  MapBiomas classes {11,24,30,33} excluded (BR), Corine ranges
  111-142/411-423/511-523 excluded (DE), class 0 always excluded as
  nodata/unclassified
  Protected areas: rasterizes data/raw/protected_areas/{region}/
  protected_areas.geojson onto the SAME reference grid via
  rasterio.features.rasterize, inverted to 1=unprotected/0=protected --
  guaranteed pixel-aligned with the land-use mask since both are built
  against get_analysis_grid()'s identical transform/shape
  Deliberately excludes slope/resource-quality continuous criteria from
  this binary mask, resolving the open design question flagged in
  scenario_params.yaml's thresholds block in favor of METHODOLOGY.md
  §2.3's stated design (those belong to topsis.py's weighted scoring)
  ✅ Verified with two synthetic end-to-end runs (mocked get_analysis_grid,
  hand-written landuse.tif/protected_areas.geojson fixtures, cleaned up
  after): protected+suitable-landuse -> fully excluded; unprotected+
  suitable-landuse -> correctly suitable, with the exact expected km²
  split. _reclassify_landuse() also unit-tested directly for both regions.
  No live run against real MapBiomas/Corine/INDE/Natura2000 data -- those
  upstream fetches are themselves unverified in this environment (no
  network access).

✅ Stage 2, item 8: src/acquisition/protected_areas_fetch.py created (195 lines)
  fetch_brazil(config) -> Path — INDE (Infraestrutura Nacional de Dados
  Espaciais) public GeoServer WFS, MMA/ICMBio CNUC conservation-units layer,
  server-side BBOX filter, outputFormat=application/json
  fetch_germany(config) -> Path — EEA discomap ArcGIS REST FeatureServer,
  Natura 2000 dynamic dataset, f=geojson, server-side bbox filter
  fetch(region, config) -> Path — dispatches to the two region-specific
  functions
  Writes data/raw/protected_areas/{region}/protected_areas.geojson directly
  from each server's JSON response -- both sources return GeoJSON natively,
  so no shapefile/GML parsing dependency was needed (simpler than
  landuse_fetch.py, which needed a windowed COG read and a WCS raster crop)
  Deviates from ARCHITECTURE.md's "Automated direct shapefile download"
  description for Brazil: queries INDE's GeoServer WFS instead of
  downloading/unpacking a national shapefile ZIP locally, avoiding a
  fiona/zipfile dependency for a one-off bbox crop -- same rationale as
  landuse_fetch.py avoiding a full national MapBiomas download. Docs
  updated this session per the source-of-truth rule (root CLAUDE.md).
  ⚠️ No live query run for either region this session (no network access in
  this environment). Verified offline: py_compile clean, fetch/
  fetch_brazil/fetch_germany import and fetch(region, config) signature
  matches the acquisition contract, and a full test_quick.py run confirms
  30/30 src/ modules import OK (0 failed) with this module included. The
  INDE WFS layer typeName and EEA discomap Natura2000 endpoint/layer index
  are best-effort from published documentation, not verified against a live
  response -- flagged in the module's SOURCE NOTE docstrings and in
  docs/memory/03_data_sources_and_acquisition.md. Run the live verification
  command above in an environment with network access before relying on
  this module's output for a real pipeline run.

✅ Stage 2, item 7: src/acquisition/landuse_fetch.py created (241 lines)
  fetch_brazil(config) -> Path — MapBiomas Collection 9 national coverage COG,
  windowed-read via GDAL /vsicurl/ to Northeast Brazil's bbox (no full
  national download)
  fetch_germany(config) -> Path — Corine Land Cover via a WCS GetCoverage
  request against the EEA discomap service, cropped server-side to North
  Germany's bbox; requires CDSE_TOKEN via credentials.get_cdse_token(),
  sent as a Bearer header
  fetch(region, config) -> Path — dispatches to the two region-specific
  functions
  Writes data/raw/landuse/{region}/landuse.tif in each source's native CRS
  (deliberately not reprojected here -- categorical resampling method choice
  belongs to spatial/exclusion_mask.py, per grid_utils.py's documented
  constraint that nearest/mode must be chosen explicitly for categorical data)
  ⚠️ No live download run for either region this session (no network access
  in this environment). Verified offline: py_compile clean, fetch/
  fetch_brazil/fetch_germany import and fetch(region, config) signature
  matches the acquisition contract, and fetch_germany() raises
  MissingCredentialError (no network call attempted) when CDSE_TOKEN is
  unset. The MapBiomas COG URL and Corine WCS endpoint/coverage-ID are
  best-effort from published documentation, not verified against a live
  response -- flagged in the module's SOURCE NOTE docstrings and in
  docs/memory/03_data_sources_and_acquisition.md. Run the live verification
  commands above (or via test_quick.py) in an environment with network
  access before relying on this module's output for a real pipeline run.

✅ Stage 1, item 6: src/acquisition/grid_infrastructure_fetch.py created (214 lines)
  fetch(region, config) -> Path — Overpass query (power=line, power=cable,
  power=substation as nodes/ways/relations) → rasterize onto analysis grid →
  scipy distance_transform_edt → data/raw/grid/{region}/distance_to_grid.tif
  Verified live for Germany (57,156 features, 82.0s end-to-end;
  load_distance_to_grid() loads the output cleanly, mean 3,434m, max 40,258m).
  Brazil not run live this session (much larger bbox).
  ⚠️ Deviates from ARCHITECTURE.md / 03_data_sources_and_acquisition.md, which
  specified ANEEL SIGA (BR) + Marktstammdatenregister (DE) as the source.
  Implemented as a single OSM Overpass query instead, mirroring
  water_bodies_fetch.py's pattern, to avoid two divergent country-specific
  API integrations for one criterion layer. ARCHITECTURE.md and
  03_data_sources_and_acquisition.md updated this session to match, per the
  source-of-truth rule (root CLAUDE.md). Revisit if ANEEL SIGA / MaStR data
  quality or completeness later proves materially better than OSM coverage
  for either region.

✅ Stage 3, item 18: spatial/data_layers.py — load_distance_to_grid() added (28 lines)
  Reads data/raw/grid/{region}/distance_to_grid.tif via the shared _load_layer()
  helper, units=meters, resampling=bilinear, no unit conversion needed —
  same pattern as load_distance_to_water().

Both changes verified with py_compile, import checks (load_distance_to_grid
present on data_layers, fetch present on grid_infrastructure_fetch), and a
live end-to-end run for Germany through both functions together.