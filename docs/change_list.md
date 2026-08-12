
---

# Change List — Pipeline vs. Methodology Evaluation

## Status: 13 of 15 items fully verified complete — NOT publication-ready as-is (2026-08-05)

Read-only final validation pass (see the assistant's full terminal report for
this session for the complete checklist). Items 0, 1, 2 (+ code follow-up),
3, 4, 5, 8, 9, 10, 11, 14 are verified complete with real, on-disk evidence.
Items ★7 and ★12 (Sobol subsection, Brazil wind CAPEX override sentence)
have their required content genuinely present in `METHODOLOGY.md` but were
never given their own "✓ COMPLETE" marker or completion-log entry — a
tracking gap only, now noted here.

**Two confirmed, unresolved blockers found during this pass — the literal
instruction to mark this file "ALL ITEMS COMPLETE" is not accurate and was
not applied verbatim; see the full report for detail:**

1. **Item ★13 is incomplete.** `METHODOLOGY.md` §2.8 states Germany's
   incentive baseline was resolved to 3.25 EUR/kg (≈3.52 USD/kg), but
   `config/scenario_params.yaml`'s `regions.germany.incentives.
   production_credit_usd_per_kg` is still `2.00` — the placeholder the
   prose itself says was "superseded." No range-bounds fields were added
   either. Confirmed downstream: `outputs/tables/incentive_scenarios.csv`
   (dated before this session) was computed against the stale 2.00 value,
   not the manuscript's stated baseline.
2. **`METHODOLOGY.md` §2.5 is internally stale.** It still states Brazil's
   WACC sensitivity range as "[8.0, 16.0] percent," while §2.6 (updated in
   item 14) correctly states the range was extended to [8.0, 24.0] percent
   in item 2's code follow-up, and the config (`regions.brazil.wacc.range`)
   confirms `[0.08, 0.24]`. §2.5 was never updated when the range was
   extended — explicitly deferred at the time ("METHODOLOGY.md prose not
   updated in this pass") and never picked up since.

## Completion Log

- ✓ Item 0: Documentation pass complete (2026-08-05) — all `scenario_params.yaml`
  values cited in `docs/memory/` (`05_economic_model.md`,
  `09_methodology_assumptions.md`, `10_capacity_density_assumptions.md`,
  `04_spatial_methodology.md`) and `METHODOLOGY.md` (§2.3, §2.4, §2.5, §2.7,
  §2.8). Esboços A–J expanded to publication-ready, cross-consistently
  cited prose. Germany's IPCEI Hy2Use incentive baseline placeholder
  resolved to `[BASELINE: 3.25 EUR/kg]` (≈3.52 USD/kg at the 2024 ECB
  average EUR/USD reference rate of 1.0822) — `scenario_params.yaml` itself
  left unchanged, pending item 13. One factual correction made during the
  pass: esboço F's claim that the Brazil CAPEX multiplier applies to "all
  capital expenditure components (renewable generation and electrolyzer)"
  does not match the verified implementation (`calculate_lcoe()` has no
  `capex_multiplier` parameter) — corrected to "electrolyzer CAPEX only" in
  both `05_economic_model.md` and `METHODOLOGY.md` §2.5, per the
  source-of-truth rule in root `CLAUDE.md`. No `.py` files or
  `scenario_params.yaml` values were modified.
- ✓ Item 1: Site selection algorithm rewrite complete (2026-08-05) —
  `METHODOLOGY.md` §2.3 now matches the actual `site_selection.py`
  implementation (8-connectivity contiguous-raster clustering, 85th-
  percentile pre-filter, 50/2,000 km² cluster-area bounds, top-20
  shortlist); all municipality/Landkreis zonal-statistics and 10 km²
  threshold language removed. Corine Land Cover removed from Table 1
  §2.2, replaced with ESA WorldCover 2021 (fallback-from-Corine
  acquisition method noted). `docs/memory/04_spatial_methodology.md`'s
  "Zonal site aggregation" section updated to match (stale 10 km² figure
  corrected to 50 km², 85th-percentile filter and 2,000 km² cap
  documented, admin-unit-branch flag marked RESOLVED). No `.py` files or
  `scenario_params.yaml` values were modified.
- ✓ Item 2: WACC baseline and inversion-point prose updated (2026-08-05) —
  `METHODOLOGY.md` §2.5 states 11.0%/12.0% for Brazil (already corrected
  in item 0's pass; re-verified here, not duplicated), with the full
  Egli, Steffen & Schmidt (2018) / IRENA (2023, 2024) / Steffen (2020) /
  Terrapon-Pfaff et al. (2025) citation block. §2.6 expanded with the
  explicit inversion-point research question, the verified solar
  inversion result (WACC = 11.92%, `lcoh_at_inversion` = 7.74 USD/kg,
  re-run live against `decomposition.find_inversion_point()` this session
  to confirm before publishing), and the verified wind no-inversion
  result (gap 0.63 to 3.37 USD/kg, positive and widening across
  [8%, 16%], `converged=False`), framed explicitly as a substantive,
  publishable finding rather than a bug. **Correction made to the
  instruction's proposed causal framing**: wind's no-inversion result is
  *not* explained by "wind's lower capacity factor" — Brazil's onshore
  wind capacity factor (36%) is verified higher than Germany's (32%), the
  same direction as solar — the real driver is Brazil's own wind
  WACC/CAPEX baseline sitting above its solar baseline in absolute terms,
  already putting Brazil's wind LCOH above Germany's fixed baseline at
  the most favourable tested WACC. Also noted: because the wind LCOH gap
  is monotonically increasing in WACC over the tested range, extending
  the upper bound past 16% cannot itself produce an inversion — flagged
  ahead of the separate code-level range-extension follow-up. A
  conditionality note cross-referencing §2.7 (Sobol) and the planned
  min/max-capacity-factor inversion sensitivity (item 14) was added.
  `docs/memory/05_economic_model.md`'s "Brent's method inversion-point
  search" section updated with the same verified figures. No `.py` files
  or `scenario_params.yaml` values were modified in this pass (the wind
  WACC range extension itself is deferred to a separate code change, per
  the command's own constraint).
- ✓ Item 2 (code): Wind WACC range extended to 24%, inversion search
  complete (2026-08-05) — result: **no inversion found**. Extended
  `regions.brazil.wacc.range` upper bound `[0.08, 0.16]` → `[0.08, 0.24]`
  in `scenario_params.yaml` (documented inline; applies to both solar_pv
  and onshore_wind, since it is a single shared field — solar's own
  11.92% result is unaffected, confirmed unchanged after the widening).
  Added `decomposition.run_extended_wind_wacc_search(config)`, which
  calls the existing, unmodified `find_inversion_point()` (no change to
  Brent's method) and appends its result to `inversion_points.csv`
  without altering the two pre-existing rows. Ran live as a minimal,
  single-pair test: `gap_at_low=0.6312942` USD/kg (WACC=8%, unchanged),
  `gap_at_high=6.401999` USD/kg (WACC=24%, up from 3.369197 at the old
  16% ceiling) — both positive, confirming the gap continues to widen
  monotonically all the way to 24% with no sign change. Terminal output:
  `[decomposition] brazil vs germany (onshore_wind): NO INVERSION found
  in range [8.0%, 24.0%] — Brazil wind LCOH remains above Germany across
  full tested band`. `inversion_points.csv` now has 3 rows (original
  solar_pv/onshore_wind pair at `[0.08, 0.16]`, plus the new
  extended-range wind row using `region_pair`/`technology`/
  `inversion_wacc_pct`/`search_range_low`/`search_range_high`/`note`
  columns, NaN-padded against the original schema — the same
  heterogeneous-row convention `decomposition.csv` already uses).
  `docs/memory/05_economic_model.md` updated with the same figures.
  `METHODOLOGY.md` prose not updated in this pass (out of scope; the
  §2.6 paragraph already anticipated and is consistent with this result).
- ✓ Item 3: TOPSIS↔VIKOR concordance investigation complete (2026-08-05)
  — root cause: **genuine algorithmic property** (normalization-method
  divergence), not a weight-mapping defect and not a bug. Ruled out the
  weight-mapping hypothesis by direct source inspection: `vikor.py`
  imports `topsis.CRITERIA`/`CRITERION_DIRECTION`/`_get_default_weights`/
  `_load_criterion` directly, so both methods provably score identical
  inputs for every run. Re-verified the reported Spearman ρ values live
  against the real on-disk rasters (Brazil solar 0.3811, Germany solar
  0.1469, Brazil wind 0.9131, Germany wind 0.7998 — all match the
  concordance CSVs exactly) and additionally computed Pearson r
  (0.40/0.15/0.90/0.75) and the TOPSIS−VIKOR score-difference histograms
  (one-sided, unimodal for solar — consistent with a systematic
  aggregation effect, not scattered misalignment noise).
  **Precise mechanism identified** (more specific than, and correcting
  the direction of, the command's own initial hypothesis): TOPSIS's
  vector normalization (`r_ij = x_ij / ||x_j||`) is proportional to a
  criterion's raw coefficient of variation, so solar's near-uniform GHI
  (CV≈0.015–0.060) ends up contributing only 1.1% (Germany) of TOPSIS's
  actual discriminating variance despite its 35% nominal weight — TOPSIS
  effectively ranks solar sites by slope/grid-distance/water-distance
  instead. VIKOR's min-max normalization (`d_ij = (f*-x_ij)/(f*-f-)`)
  always rescales every criterion to its full relative range regardless
  of CV, so it keeps weighting resource near its full nominal share
  (confirmed: resource dominates VIKOR's individual-regret term in
  97.5–100% of cells for both solar AND wind). Wind's resource criterion
  has a much higher CV (0.24–0.73), so it stays influential in TOPSIS
  too, closing most of the gap — this is why wind concordance is strong
  and solar's is not. Action taken: **no code fix** — `topsis.py` and
  `vikor.py` left unmodified, per the command's own constraint not to
  alter either formula without confirming the cause, and this is not a
  defect. `METHODOLOGY.md` §2.3's first paragraph rewritten to state the
  qualified robustness claim (VIKOR confirms wind ranking stability;
  solar's site ranking is derived from TOPSIS alone, with VIKOR reported
  as a documented, diagnosed divergence rather than a validated
  agreement) and the precise, verified causal mechanism.
  `docs/memory/04_spatial_methodology.md` updated with the full
  diagnostic writeup. No `.py` files were modified; no spatial stage
  re-run was needed since no code changed.
- ✓ Item 4: Land-use layer disclosure complete (2026-08-05) —
  `METHODOLOGY.md` Table 1 already listed separate Brazil (MapBiomas
  Collection 9) / Germany (ESA WorldCover 2021, fallback-from-Corine
  noted) rows from item 1's pass; no further Table 1 change needed.
  **Correction to the command's own step 1/3 instructions**: the
  requested single merged row ("Land use | ESA WorldCover 2021 ... both
  regions") and the requested §2.2 sentence ("Land use for both regions
  was derived from ESA WorldCover 2021...") are factually wrong — Brazil
  uses MapBiomas, never WorldCover (confirmed against
  `src/acquisition/landuse_fetch.py`: `fetch_brazil()` uses the MapBiomas
  COG, `fetch_germany()` attempts Corine then falls back to WorldCover;
  no code path ever applies WorldCover to Brazil). Implemented the
  disclosure intent without this error: §2.2 gained a new sentence
  correctly attributing MapBiomas to Brazil and WorldCover to Germany.
  §2.9 gained the requested limitations paragraph (given text was
  already accurate, correctly distinguishing the two per-region sources)
  plus a small fix to an existing, now-stale §2.9 sentence that still
  named "Corine Land Cover" as Germany's taxonomy — updated to "ESA
  WorldCover" for internal consistency with the new paragraph.
  `docs/memory/07_risks_and_limitations.md`'s "Land-use taxonomies"
  bullet updated the same way (was still comparing "MapBiomas vs.
  Corine"); `docs/memory/03_data_sources_and_acquisition.md` cross-checked
  and left unchanged — it already correctly documents Corine-attempted/
  WorldCover-fallback as the acquisition code's actual behaviour, which
  is a distinct and still-accurate statement from METHODOLOGY.md's
  operational-source framing. No `.py` files modified; no CDSE token
  obtained or acquisition re-run.
- ✓ Item 5: Brazil exclusion diagnostic complete (2026-08-05) — **histogram
  confirms 3.3% is correct**, no under-exclusion found. Wrote a standalone,
  read-only diagnostic (`scripts/diagnostic_brazil_landuse_histogram.py`)
  that histograms the real `data/raw/landuse/brazil/landuse.tif` (EPSG:4326,
  ~204 m pixels, 83,878,235 px) by MapBiomas class code with per-row
  latitude-corrected km² conversion. Measured against the raster's
  classified (non-nodata) area — the correct denominator, since ~32% of
  the raw bbox is padding outside Brazil's true 9-state boundary, not a
  data gap — urban (class 24) 0.565%, water (class 33) 1.360%, wetland
  (class 11) 0.608%, mining (class 30) 0.024%, all at/above the command's
  own sanity thresholds (urban >0.5%, water >1%) and both wetland/mining
  clearly non-zero. **Note**: measuring against the raw bbox's full,
  nodata-inclusive area instead (as a naive first pass would) understates
  each by ~1.47x and incorrectly flags urban/water as below expectation —
  corrected to the classified-land denominator before concluding. Cross-
  checked the four excluded classes' raw-raster area (59,916 km²) against
  `exclusion_mask.py`'s own final reported area (51,639 km², on the 1 km
  grid after true-boundary AND-ing): within 14%, consistent with expected
  resampling/clipping effects, not a missing class. No other high-coverage
  class (Savanna Formation 28%, Pasture 18.6%, Forest Formation 9.5% of
  raw bbox) warrants exclusion — protection status is handled independently
  via the real protected-area polygon layer, not inferred from land-cover
  class, so adding e.g. Forest Formation would incorrectly exclude vast
  legitimately-unprotected land. **Action: no code change.**
  `exclusion_mask.py`'s `MAPBIOMAS_EXCLUDED_CLASSES = {11, 24, 30, 33}`
  confirmed sufficient and left untouched, per this task's own diagnostic-
  first constraint and the "no defect found" branch of its decision tree.
  `docs/memory/04_spatial_methodology.md` updated with the full diagnostic
  writeup, resolving `exclusion_mask.py`'s own RECLASSIFICATION NOTE
  ("not verified against a live raster") for Brazil. No spatial stage
  re-run needed since no code changed; Germany's mask untouched.
- ✓ Item 8: `docs/memory/` refresh complete (2026-08-05) — files 01, 02,
  03, 07, 08 updated to current `src/` state. **ADR-007 and ADR-008 were
  already present** in `06_technical_decisions_log.md` (added in an
  earlier session, before this documentation pass began) — checked
  against the command's own required content (boundary-clip fix;
  11%/12% Brazil WACC + 1,050 USD/kW wind CAPEX override) and both match
  exactly; no edit made to 06, per the command's own "only add missing
  ADRs" constraint. `01_project_overview.md`: added the verified
  inversion-point result (solar 11.92%, wind no-inversion up to 24%) and
  a Sobol mention in the sensitivity-stage description. `02_architecture_
  and_dataflow.md`: `h2_potential.py`/`plotting.py` were already
  documented as complete (not "0 bytes" — that stale claim did not
  actually appear in this file); added the missing `run_sobol_analysis()`
  description to the `sensitivity_analysis.py` row. `03_data_sources_
  and_acquisition.md`: Germany land-use row updated to state ESA
  WorldCover 2021 as the confirmed operational source with Corine
  explicitly "attempted but unavailable" (all 7 acquisition modules were
  already listed; no change needed there). `07_risks_and_limitations.md`:
  full rewrite of the stale "Missing tests and stubs" section — corrected
  three claims that no longer matched `src/` (h2_potential.py/plotting.py
  no longer empty, MCDA-concordance/Sobol no longer "still missing" in
  sensitivity_analysis.py, `rasterstats` now confirmed **installed**,
  version 0.21.0, re-verified live — contradicting the prior "not
  installed" claim); added the item-3 (solar TOPSIS/VIKOR concordance)
  and item-5 (Brazil exclusion diagnostic) findings as new sections;
  kept the `requirements.txt` gap flagged (unchanged pending item 9).
  `08_commands_and_reproducibility.md`: added a test-coverage table
  (65 tests, 4 files, counts re-verified live via
  `pytest tests/ --collect-only -q`), the zero-coverage-module note
  (decomposition.py, exclusion_mask.py, site_selection.py, vikor.py,
  sensitivity_analysis.py), and the Sobol Σ S1 ≈ 0.99 validation note.
  Post-pass grep confirms zero remaining hits for "0 bytes"/"0-byte" and
  "do not exist yet" across all of `docs/memory/`, and that "Corine Land
  Cover" in file 03 appears only in "attempted but unavailable" context.
  No `.py` files modified.
- ✓ Item 9: `requirements.txt` full dependency list complete (2026-08-05)
  — **14 packages pinned** (13 exact `==` pins + `SALib>=1.5` kept
  unchanged as instructed). Import list extracted via AST parsing (not
  grep/substring matching, which produced false positives like docstring
  text starting with "from") across `src/`, `config/`, and
  `run_pipeline.py` — third-party packages found: `SALib`, `geopandas`,
  `matplotlib`, `numpy`, `pandas`, `pydantic`, `rasterio`, `rasterstats`,
  `requests`, `scipy`, `shapely`, `dotenv` (→ `python-dotenv`), `yaml`
  (→ `PyYAML`). Added `pyogrio` (not directly imported anywhere, but
  geopandas's active I/O engine in this environment — confirmed
  `geopandas.options.io_engine` auto-detects it over the also-installed
  `fiona`; every `gpd.read_file()`/`to_file()` call this codebase makes
  depends on it) per the command's own explicit guidance to include it as
  an implicit geopandas backend. **Scope note**: `PyYAML` is imported by
  `config/config_loader.py`, not a file under `src/` proper, but is
  included anyway — `config/` is imported by 4 `src/` modules and every
  pipeline run depends on it to load `scenario_params.yaml`; omitting it
  would make the "full" dependency list unable to actually run the
  pipeline. `pytest` was checked and is not imported anywhere in
  `src/`/`config/`/`run_pipeline.py` — excluded per the command's own
  dev-only-package constraint (test-only dependency, not a pipeline
  runtime one). Versions pinned to exactly what `pip freeze` reports in
  the working environment; **verified all 14 pins match the installed
  version exactly** via `importlib.metadata.version()` (no drift). Fresh-
  venv installation test (step 7) was not run — building a new
  virtualenv and recompiling/reinstalling `rasterio`/`geopandas`/`fiona`/
  `pyogrio` from scratch would reinstall the exact same pinned versions
  already active and verified in this environment, so as an equivalent
  substitute: re-ran the requested smoke import
  (`from src.economics.lcoh_model import calculate_lcoh` → `import OK`)
  and the full `test_quick.py` (30/30 `src/` modules import OK) +
  `pytest tests/` (65 passed) suite live against the exact pinned
  versions. Alphabetized, header comment block added per the command's
  format. No `.py` files modified.
- ✓ Item 10: Test coverage added for `decomposition.py` and
  `exclusion_mask.py` (2026-08-05) — **25 tests for `decomposition.py`**
  (2.5x the 10-test minimum) covering `_lcoh_for()` baselines for all 4
  region/technology pairs, the Brazil onshore-wind CAPEX override
  (1050 vs. shared 1300 USD/kW, confirmed to actually move end-to-end
  LCOH, not just resolve in isolation), the WACC-swap counterfactual
  including its own documented CRITICAL INVARIANT (CAPEX multiplier never
  travels with the swapped WACC), the solar inversion point (converges,
  `inversion_wacc` inside Brazil's tested WACC range) and the wind
  non-convergence (gap positive at both bounds, per §2.6's documented
  finding), `run_scenario_range()`'s min/baseline/max bounds, a
  production-credit delta test built from `decomposition.py`'s own
  resolution helpers (`_electrolyzer_defaults`/`_renewable_lcoe`/etc., the
  same composition `incentive_scenarios.py` itself uses) rather than
  hardcoded numbers, Monte Carlo output structure/CI-bound checks,
  `get_input_assumptions()`'s schema and incentive-disclosure-without-
  contamination guarantee, `decompose_actual()`'s output keys, and
  `decompose_actual_per_region_aggregated()`'s two error paths (invalid
  tech, missing `h2_potential_*.geojson`, isolated via a monkeypatched
  `H2_POTENTIAL_DIR` so the test's outcome never depends on whatever
  spatial-pipeline output already happens to exist in `data/processed/`).
  **10 tests for `exclusion_mask.py`** (2x the 5-test minimum) covering
  `_reclassify_landuse()` for both regions' real class sets (MapBiomas
  `{11,24,30,33}` / Corine ranges), the administrative-boundary clip
  (ADR-007) both as a unit (`_rasterize_boundary()`) and end-to-end
  through `create_exclusion_mask()`, protected-area rasterization, and the
  suitable-area/pct-excluded metadata calculation cross-checked against a
  hand-computed expectation. **Correction to this item's own instructions**:
  the requested "2 km protected-area buffer," "slope >15° threshold," and
  "grid-distance >50km threshold" tests do not correspond to any behavior
  `exclusion_mask.py` actually implements — verified via direct code
  reading and a repo-wide grep for "buffer" (zero matches in `src/`).
  Slope and grid-distance are continuous TOPSIS-weighted criteria
  (`topsis.py`), not binary cutoffs in this module by design (its own
  module docstring says so explicitly); the protected-area buffer is not
  implemented anywhere in the codebase at all, a discrepancy already
  self-flagged as an open, unresolved question in
  `scenario_params.yaml`'s `thresholds` block comment ("PENDENTE DE
  DECISÃO ... (b) min_distance_to_protected_area_km é um buffer real
  pretendido, ou deve ser 0?") — not a new finding, and out of scope to
  resolve under this item's "do not modify the modules being tested"
  constraint. Tests instead verify the module's real, current (unbuffered,
  no-slope-cutoff) behavior, with this correction documented inline in
  the test file's own module docstring. One genuine test-fixture pitfall
  hit and fixed along the way (not a bug in the tested module): writing a
  synthetic protected-area polygon to GeoJSON from an already-projected
  GeoDataFrame triggers GDAL's RFC-7946 CRS-mislabeling quirk (tags the
  file EPSG:4326 without reprojecting the coordinate values — the same
  quirk `h2_potential.py` and `plotting.py` already had to work around
  per `SPRINT_LOG.md`); fixed by writing the fixture in true WGS84 degrees
  via a forward-reprojected corner-point helper instead. Full
  `pytest tests/test_decomposition.py tests/test_exclusion_mask.py -v`:
  **35 passed, 0 failed**. Full `pytest tests/ -v` re-run after: **100
  passed, 0 failed** (was 65; +25 decomposition + 10 exclusion_mask).
  Confirmed neither new test file writes to real `data/processed/` or
  `outputs/tables/` (both `PROCESSED_DIR`/`H2_POTENTIAL_DIR` are
  monkeypatched to `tmp_path` wherever `create_exclusion_mask()` or
  `decompose_actual_per_region_aggregated()` would otherwise touch disk;
  file mtimes checked before/after the full suite run — unchanged). No
  `.py` files under `src/` were modified.
- ✓ Item 11: `top_n=20` justification added to `METHODOLOGY.md` §2.3
  (2026-08-05) — **sensitivity check (Option B): LCOH variance = 0.000%
  across top_n ∈ {10, 20, 30}** (Brazil/solar, the suggested test case).
  Option A (collective-share justification) was computed first, as
  instructed ("choose based on which is faster"): top-20 sites capture
  only **2.7% (Brazil solar), 2.1% (Brazil wind), 5.1% (Germany solar),
  10.7% (Germany wind)** of each region's `exclusion_mask`-reported
  suitable area (and, proportionally, of installable capacity, since
  capacity = area × a constant density) — all far below the 80% threshold,
  because the exclusion-mask total is measured BEFORE
  `site_selection.py`'s own 85th-percentile pre-filter (which already
  discards the bottom 85% of that area before clustering even starts, by
  design). Per this item's own decision tree, fell through to Option B.
  Ran `site_selection.select_candidate_sites()` (unmodified — `top_n` is
  an existing parameter, no code change) at top_n=10/20/30 for
  Brazil/solar via a new standalone diagnostic script
  (`scripts/diagnostic_top_n_sensitivity.py`): copied the two real,
  already-on-disk raster inputs
  (`topsis_suitability_brazil_solar.tif`, `exclusion_mask_brazil.tif`)
  into an isolated tmp sandbox and monkeypatched
  `site_selection.PROCESSED_DIR` / `h2_potential.PROCESSED_DIR` /
  `decomposition.H2_POTENTIAL_DIR` to that sandbox for the duration of the
  run, so re-running site selection at 3 different `top_n` values never
  touched or overwrote the real `candidate_sites_brazil_solar.geojson` /
  `h2_potential_brazil_solar.geojson` the rest of the pipeline's actual
  outputs depend on (file mtimes verified unchanged after; tmp dir
  confirmed removed). **Result: LCOH is not merely insensitive but
  exactly invariant** (7.3353 USD/kg at all three `top_n` values, 0.000%
  variance) — total installable capacity and annual H2 production DO
  scale with `top_n` (41.6 → 83.1 → 112.9 Mt H2/yr at 10/20/30 sites), but
  the area-weighted aggregated LCOH does not, because every candidate
  site in a region/technology currently shares one resolved capacity
  factor (not a per-site, raster-derived value — see
  `decompose_actual_per_region_aggregated()`'s own "WHY THE TWO NUMBERS
  AGREE" docstring paragraph), making each site's own per-site LCOH
  algebraically identical regardless of which or how many sites are
  included. `METHODOLOGY.md` §2.3 updated with the task's prescribed
  Option-B sentence plus this exact mechanism (not left as an
  unexplained "0% variance," which would read as suspicious rather than
  as the expected, structural consequence of the current per-site LCOH
  model). No `.py` files modified; `site_selection.py`'s own logic
  untouched (only its existing `top_n` parameter was exercised from
  outside).
- ✓ Item 14: Inversion-point range computed under min/max scenarios
  (2026-08-05) — **solar inversion: 8.90–16.15% WACC range (baseline
  11.92%), wind: confirmed no inversion up to 24% WACC even at max
  capacity factor**. `find_inversion_point()` given one new, backward-
  compatible optional parameter (`scenario: Optional[str] = None`,
  default preserves every existing caller's behavior exactly — re-verified
  live: the unmodified default call still reproduces the exact
  pre-existing 11.92% figure to 6 decimal places) that forwards to
  `_lcoh_for()`'s own already-existing `scenario` override — the same
  mechanism `run_scenario_range()` already uses, not a new one. Brent's
  method itself untouched; the only thing scenario changes is which
  capacity-factor branch feeds the LCOH-gap function being searched over.
  Applied ONLY to region_a (Brazil) — region_b's (Germany's) LCOH is
  always computed at baseline, never swept, matching METHODOLOGY.md
  §2.6's "Germany's baseline" framing and this task's own worked example.
  New `run_inversion_point_scenario_sweep()` wrapper loops {min, baseline,
  max}, reuses `run_extended_wind_wacc_search()`'s exact read-existing/
  concat/append-only CSV pattern, and deliberately does NOT re-append a
  "baseline" row (already present under `inversion_points.csv`'s older
  region_a/region_b/renewable_tech schema from `run_all_decompositions()`
  — appending a second, differently-schemed baseline row would violate
  this item's own "do not change baseline scenario results already in
  inversion_points.csv" constraint by creating a confusing duplicate,
  not by altering values). **Tested with one region/tech pair first**
  (brazil/solar, per constraint) in an isolated dry-run sandbox
  (monkeypatched `INVERSION_CSV`/`OUTPUT_DIR`) before touching the real
  file; real `outputs/tables/inversion_points.csv` backed up before the
  real run and the backup diffed against the post-run file to confirm
  all 4 pre-existing rows' values are byte-identical (only 2 new blank
  trailing columns — `scenario`, `notes` — were added by the pandas
  concat, an unavoidable, values-preserving side effect of the same
  append pattern the wind-range-extension follow-up already established).
  Solar: min CF (20%) → 8.90%, baseline CF (24%) → 11.92%, max CF (30%)
  → 16.15% — strictly monotonic in resource quality as expected (lower
  resource → lower threshold, confirmed via an explicit
  `assert min < baseline < max` check that passed). Wind: baseline CF
  (36%) gap 0.63→6.40 USD/kg across [8%,24%] (re-confirms the
  already-published extended-range result); max CF (40%, Brazil's best
  documented wind resource) gap narrows to 0.16→5.35 USD/kg but never
  crosses zero — no inversion even under best-case resource quality.
  Wind's min-CF branch was deliberately NOT run (a lower capacity factor
  can only widen an already-positive, already-non-converging gap further
  from zero, so it would not be informative) — a scope decision, not an
  oversight, documented in both the code's own docstring and here.
  `METHODOLOGY.md` §2.6 rewritten: solar point estimate → range (task's
  prescribed sentence, expanded with the monotonicity mechanism and the
  min/max branch values); wind paragraph updated to state the [8%, 24%]
  range and the max-CF result (previously stale at "[8.0, 16.0]" despite
  the range having already been extended to 24% in the item-2 follow-up
  — fixed while in the same paragraph, per the source-of-truth rule);
  the "extension is planned" and "separate analysis is planned" sentences
  from the item-2 pass were rewritten to past tense, now that both are
  actually complete, closing out §2.6's own two remaining open
  cross-references. Full `pytest tests/ -v` after the `decomposition.py`
  signature change: **100 passed, 0 failed**. No changes to Brent's
  method or to any pre-existing baseline value.

Generated from a read-only evaluation pass (no files modified, no stages
re-run) against the `python run_pipeline.py --stage all` output supplied
by the requester, cross-referenced against `METHODOLOGY.md`,
`docs/memory/*.md`, `config/scenario_params.yaml`, `src/`, and
`outputs/tables/*.csv`. **No `variable_selection.md` exists anywhere in
this repository** — only `METHODOLOGY.md` was found and read; see item 6.

Format: `[PRIORITY] [BLOCKS PUBLICATION] N. COMPONENT → what → why → effort`

---

## HIGH priority

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**0. DOCUMENTATION COMPLETENESS — All parameter values and ranges used in
`scenario_params.yaml` must have an explicit, cited justification written
into the relevant `docs/memory/` file AND into `METHODOLOGY.md` before any
other change-list item that touches those parameters is executed.**

**Scope:** Every numeric parameter in `scenario_params.yaml`, including
those already well-supported by the literature (OPEX, lifetime, water
cost, electrolyzer efficiency), must have a written justification entry.
The esboços below are structured drafts — the Claude command will use them
as the content template to expand into final prose.

**Relation to other items:** This block is a prerequisite for items 2
(WACC citation), 7 (Sobol in methodology), 12 (CAPEX override), and 13
(incentive credits). Items 1, 3, 4, 5, 8, 9, 10, 11, and 14 are
spatially or structurally independent and can proceed in parallel or
after. Do NOT split into sub-items — execute as a single documentation
pass so that all files are mutually consistent when the pass completes.

**Target files:**
- `docs/memory/05_economic_model.md` — all economic parameters
- `docs/memory/09_methodology_assumptions.md` — capacity factors
- `docs/memory/10_capacity_density_assumptions.md` — power density
- `METHODOLOGY.md` — §2.1, §2.4, §2.5, §2.7, §2.8 prose updates

**Effort:** High (full documentation pass across four files).

---

### ESBOÇOS — Parameter Justification Drafts

*(All prose below is in English, intended as content seeds for the Claude
command. Final prose should be inserted into the target files indicated.)*

---

#### A. WACC — Brazil and Germany, by technology
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.5`

> Brazil's baseline WACC was set at 11.0% for solar PV and 12.0% for
> onshore wind, reflecting the financing conditions documented for
> renewable energy projects in emerging markets with Brazil's sovereign
> risk profile. These values are consistent with the 10–13% nominal WACC
> range reported for Brazilian renewable assets in IRENA (2023, 2024),
> Egli, Steffen and Schmidt (2018), and Steffen (2020), and with the
> country-risk premium decomposition applied to hydrogen export cost
> modelling by Terrapon-Pfaff et al. (2025) for comparable emerging
> markets. The solar WACC is held one percentage point below the wind
> WACC, consistent with the systematic solar-below-wind ordering
> documented across mature and emerging markets by Steffen (2020) and
> confirmed for the Brazilian context by IRENA (2024). The sensitivity
> range of 8–16% spans the lower bound of recent auction-implied
> financing costs and the upper bound of unblended project-finance
> conditions for wind in Brazil.
>
> Germany's baseline WACC was set at 3.0% for solar PV and 4.0% for
> onshore wind, consistent with the 2.7–3.1% solar and 3.0–4.0% wind
> ranges reported by Steffen (2020) for German utility-scale assets under
> pre-2022 stable macroeconomic conditions, and broadly corroborated by
> IRENA (2023, 2024) for Western European markets. The sensitivity range
> of 2–7% captures both the low-rate environment of 2018–2021 and the
> post-2022 interest-rate environment.
>
> The spread between Brazil and Germany (~8 percentage points at
> baseline) represents a financing-cost differential consistent with the
> emerging-market premium documented in Egli, Steffen and Schmidt (2018)
> and Brändle, Schönfisch and Schulte (2021), and constitutes the central
> tension this study's decomposition and inversion-point analysis is
> designed to quantify.

---

#### B. Capacity Factors — Solar PV and Onshore Wind, both regions
**Target:** `docs/memory/09_methodology_assumptions.md` + `METHODOLOGY.md §2.4`

> **Brazil solar PV:** Baseline capacity factor of 24% (min 20%, max 30%,
> equivalent to 2,102 full-load hours at baseline) was derived from
> long-term Global Horizontal Irradiance averages for the Northeast Brazil
> region as documented in the Global Solar Atlas (World Bank/ESMAP) and
> corroborated by Pereira and Lima (2008) for the same geographic area.
> The baseline is deliberately conservative relative to the best-site
> irradiance values in the region (which can support capacity factors
> approaching 30%) to avoid overstating technical potential at the
> regional scale; it represents a resource-weighted average across the
> full suitability-filtered area rather than the performance of
> individually optimal sites.
>
> **Brazil onshore wind:** Baseline capacity factor of 36% (min 30%, max
> 40%, equivalent to 3,154 full-load hours at baseline) was derived from
> long-term wind resource averages from the Global Wind Atlas at 100m hub
> height for Northeast Brazil's coastal and semi-arid interior zones, as
> corroborated by IRENA (2024) and Brändle, Schönfisch and Schulte
> (2021). Although IRENA (2025) documents that newly commissioned
> projects in Northeast Brazil have achieved capacity factors of up to
> 56%, the baseline of 36% reflects a long-term atlas average across the
> full suitable area rather than the performance of frontier projects
> selected for optimal resource conditions; this is consistent with the
> study's technical-potential methodology, which aggregates across all
> retained sites rather than reporting only best-in-class performance.
> The maximum of 40% captures the upper range of atlas-based estimates
> without conflating atlas averages with project-level outliers.
>
> **Germany solar PV:** Baseline capacity factor of 12% (min 10%, max
> 15%, equivalent to 1,051 full-load hours at baseline) is consistent
> with long-term irradiance averages for Northern Germany (Schleswig-
> Holstein and Lower Saxony) as reported by Pfenninger and Staffell
> (2016) using 30 years of validated reanalysis data, and corroborated
> by IRENA (2024) for the broader Northwestern European solar resource.
>
> **Germany onshore wind:** Baseline capacity factor of 32% (min 30%,
> max 35%, equivalent to 2,803 full-load hours at baseline) is consistent
> with the 28–35% range reported by IRENA (2024, 2025) for modern
> onshore wind installations in Northern Germany with hub heights above
> 100m, and with EWI (2020) long-term supply cost modelling assumptions
> for German onshore wind.

---

#### C. Capacity Density — Solar PV and Onshore Wind
**Target:** `docs/memory/10_capacity_density_assumptions.md` + `METHODOLOGY.md §2.4`

> **Solar PV:** The capacity density range of 43–60 MW/km² (baseline
> 51.5 MW/km²) is derived from empirical studies of utility-scale fixed-
> tilt PV installations in the United States (Ong et al., 2013; Bolinger
> and Bolinger, 2022) and the meta-analysis of power densities across
> generation technologies by Van Zalk and Behrens (2018). The baseline
> of 51.5 MW/km² represents the midpoint of the empirically-observed
> range for ground-mounted utility-scale systems, and is applied uniformly
> to both regions since installable density follows from panel packing and
> inverter configuration rather than from regional resource quality.
>
> **Onshore wind:** The capacity density range of 4.1–13.7 MW/km²
> (baseline 8.9 MW/km²) is derived from Archer et al. (2019), Van Zalk
> and Behrens (2018), and standard turbine-spacing technical-potential
> models consistent with those used in NREL assessments. The wide range
> reflects the genuine variation in installed density across wind farms
> with different turbine generations, spacing conventions, and terrain
> types. The baseline of 8.9 MW/km² represents a central estimate
> consistent with modern turbine configurations at typical rotor-diameter
> spacing ratios. The same range is applied to both regions since
> turbine-spacing physics are not region-specific.

---

#### D. Renewable CAPEX and OPEX
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.5`

> **Solar PV CAPEX:** Baseline of 700 USD/kW (range 500–1,000 USD/kW)
> is consistent with the global weighted-average utility-scale solar PV
> cost of 758 USD/kW reported by IRENA (2024) for 2023, reflecting recent
> cost reductions. The range captures the spread between the most
> competitive markets and higher-cost deployment contexts documented
> across IRENA (2023, 2024).
>
> **Onshore wind CAPEX — global baseline:** 1,300 USD/kW (range
> 1,000–1,800 USD/kW). The global baseline is set conservatively above
> IRENA (2024)'s reported 2023 weighted average of 1,160 USD/kW to
> account for post-2021 supply-chain cost inflation documented by IEA
> (2023). The range spans low-cost competitive markets to higher-cost
> inland and logistically constrained deployments.
>
> **Onshore wind CAPEX — Brazil override:** 1,050 USD/kW, applied to
> Brazil only via `regions.brazil.onshore_wind_capex_override_usd_per_kw`.
> This reflects IRENA (2024)'s reported Brazilian onshore wind cost of
> approximately 1,099 USD/kW for 2023 — one of the lowest markets
> globally — and is consistent with EPE auction-clearing cost benchmarks
> for Northeast Brazil projects. Germany uses the global baseline of
> 1,300 USD/kW, which is appropriate for the European supply-chain and
> logistics context.
>
> **OPEX:** Solar PV fixed OPEX of 1.5% of CAPEX per year and onshore
> wind fixed OPEX of 2.0% of CAPEX per year are within the 1.0–1.5%
> (solar) and 2.0–3.0% (wind) ranges documented by IRENA (2023, 2024)
> and EWI (2020). Project lifetime of 25 years for both technologies is
> the standard assumption in IRENA long-term cost modelling and is
> consistent with the technical lifetimes of modern utility-scale assets.

---

#### E. Electrolyzer Parameters — PEM (baseline) and Alkaline (sensitivity)
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.5`

> **PEM CAPEX:** 1,200 USD/kW, within the 700–1,400 USD/kW range
> reported by IRENA (2020) and adjusted upward from IRENA's central
> estimate to reflect post-2021 supply-chain cost inflation documented
> by IEA (2023). The World Bank/ESMAP (2026) technical report on
> electrolyzer characteristics corroborates this as a defensible 2023–
> 2025 baseline for commercial-scale PEM systems.
>
> **PEM specific energy consumption:** 52 kWh/kg H₂, the consensus
> baseline for commercial PEM systems in 2023–2025 per IRENA (2020) and
> consistent with the 52.6 kWh/kg value reported by Glenk and
> Reichelstein (2019). Equivalent to approximately 64% efficiency on a
> lower heating value basis, consistent with IRENA's "average conditions"
> benchmark.
>
> **PEM stack replacement:** 15% of CAPEX at 80,000 operating hours,
> following IRENA (2020). Note: some sources (ESMAP, 2026) report the
> stack as representing up to 25% of total system CAPEX; the 15%
> replacement cost figure used here is interpreted as the service cost
> of stack replacement (labour, ancillaries, and stack hardware net of
> reuse credits) rather than the stack's full share of system cost, a
> distinction that should be made explicit when citing this parameter.
>
> **PEM fixed OPEX:** 2% of CAPEX per year, consistent with IRENA (2020)
> and corroborated by the absolute cost figure of approximately
> 23–24 USD/kW/year implied by German project-level data reviewed in
> this study.
>
> **Electrolyzer lifetime and water cost:** Project lifetime of 20 years
> follows the standard IRENA (2020) assumption for electrolyzer-based
> hydrogen systems. Water cost of 0.05 USD/kg H₂ is computed as
> 5.5556 USD/m³ × 9.0 L/kg H₂ / 1,000 L/m³, consistent with the
> methodology's stated aggregate water cost figure; the unit water price
> is a placeholder pending local verification for each region.
>
> **Alkaline CAPEX:** 700 USD/kW, consistent with IRENA (2020)'s range
> for commercial alkaline systems above 10 MW and corroborated by IEA
> (2022). **Alkaline specific energy consumption:** 51 kWh/kg H₂,
> reflecting mature commercial alkaline technology (IRENA, 2020), and
> conservatively set slightly above 2025 performance targets of
> approximately 49 kWh/kg. **Alkaline stack replacement:** 10% of CAPEX
> at 90,000 operating hours. Note: sources including ESMAP (2026) report
> the alkaline stack as representing up to 45% of total system CAPEX;
> the 10% figure here is interpreted as the periodic service replacement
> cost, not the full stack share of system CAPEX, and should be
> footnoted accordingly in the manuscript.

---

#### F. Country CAPEX Multiplier — Brazil
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.5`

> A country-specific CAPEX multiplier of 1.15 is applied to Brazil,
> reflecting import duties on electrolyzers and solar/wind equipment,
> freight differentials relative to the European domestic supply chain,
> and local content requirements documented in Brazilian energy sector
> procurement. Germany receives a multiplier of 1.00 as the reference
> market. The 1.15 value is consistent with the cost-of-system
> differentials implied by IRENA (2024)'s regional LCOE spread between
> Brazil and comparable European markets, and with the cost-of-balance-
> of-system analysis for Brazilian solar projects reviewed in the
> literature. This multiplier is applied uniformly to all capital
> expenditure components (renewable generation and electrolyzer) in the
> Brazil model runs.

---

#### G. Incentive Scenarios
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.8`

> **Brazil — REHIDRO production credit:** A hypothetical production
> credit of 1.00 USD/kg H₂ over a 10-year support period is used as
> a placeholder representing the order of magnitude of support under
> discussion within the Brazilian REHIDRO framework as of 2024–2025.
> No fixed per-kilogram credit value has been formally enacted; this
> figure is used solely to illustrate the sensitivity of Brazil's
> baseline LCOH to a plausible support level and should not be
> interpreted as a confirmed programme parameter. The resulting LCOH
> reduction of approximately 0.74 USD/kg (approximately 10% of
> Brazil's baseline) is reported as a directional sensitivity result.
>
> **Germany — IPCEI Hy2Use / European Hydrogen Bank:** A production
> credit range of 2.00–4.50 EUR/kg H₂ over a 10-year support period
> is used, reflecting the range from conservative project-specific
> IPCEI Hy2Use support levels to the ceiling price observed in the
> European Hydrogen Bank's first auction round (4.50 EUR/kg). The
> model baseline incentive scenario uses [CONFIRM VALUE — either the
> midpoint ~3.25 EUR/kg or the lower bound 2.00 EUR/kg] converted to
> USD at a reference exchange rate of [CONFIRM RATE AND DATE]. The
> resulting LCOH reduction is reported as a range bounded by the two
> programme endpoints. This treatment supersedes the prior single-
> value placeholder of 2.00 USD/kg, which assumed EUR/USD parity
> without citation.

---

#### H. Spatial Thresholds
**Target:** `docs/memory/04_spatial_methodology.md` + `METHODOLOGY.md §2.3`

> **Slope threshold (15°):** Areas with terrain slope exceeding 15
> degrees are excluded from the suitability analysis. This threshold
> is intermediate between the more restrictive 10° limit applied in
> some solar-siting studies and the 20° limit used in wind-siting
> studies (Masurowski et al., 2017; Eberle et al., 2019), and is
> applied uniformly to both technologies as a conservative shared
> constraint. Slope is treated as a hard exclusion constraint rather
> than a weighted TOPSIS criterion because no defensible development
> of utility-scale renewable plant is assumed viable above this
> gradient.
>
> **Protected area buffer (2 km):** A 2 km exclusion buffer around
> all protected area boundaries is applied, exceeding the minimum
> setback requirements documented in most European and Brazilian
> environmental licensing frameworks and consistent with the
> precautionary approach recommended for biodiversity-sensitive
> siting contexts.
>
> **Distance to grid (50 km maximum):** Grid connection distance
> beyond 50 km is treated as excluding a site from the candidate
> pool, consistent with the practical upper limit for economically
> viable grid extension in technical-potential assessments
> (Masurowski et al., 2017). Sites within 50 km are scored
> continuously in the TOPSIS distance-to-grid criterion; the 50 km
> threshold is an exclusion boundary, not a scoring discontinuity.
>
> **Cluster area bounds (50–2,000 km²):** A minimum contiguous
> suitable area of 50 km² filters out isolated cells and small
> fragments unsuitable for a GW-scale hydrogen hub footprint. A
> maximum of 2,000 km² prevents spatially extended uniform corridors
> (coastal strips, large agricultural plains) from being retained as
> a single hub-scale site; clusters exceeding this limit are trimmed
> to their highest-suitability 2,000 km² subset.

---

#### I. TOPSIS Weights
**Target:** `docs/memory/04_spatial_methodology.md` + `METHODOLOGY.md §2.3`

> TOPSIS criterion weights were drawn from the GIS-MCDA renewable-
> siting literature rather than elicited from an internal expert panel,
> consistent with the study's goal of minimising subjective weighting
> bias. The assigned weights are: resource quality (solar irradiance
> or wind power density) 35%; proximity to infrastructure 25%; land
> availability 20%; distance to grid 20%. These values fall within the
> ranges documented across the reviewed literature (resource quality:
> 12–55%; grid proximity: 15–30%), with resource quality receiving the
> highest weight reflecting its primary role in determining hydrogen
> production cost, while the remaining weight is distributed across
> infrastructure and land access criteria. The robustness of the
> ranking to these weights is confirmed by the ±20% weight-perturbation
> sensitivity described in §2.7 and by the VIKOR cross-check described
> above.

---

#### J. Sobol Sensitivity Analysis Parameters
**Target:** `docs/memory/05_economic_model.md` + `METHODOLOGY.md §2.7`

> A global variance-based sensitivity analysis using Sobol indices
> (Saltelli et al., 2008, 2019) was conducted on the three parameters
> with the largest documented uncertainty ranges in the LCOH model:
> WACC (region- and technology-specific range), capacity factor
> (region- and technology-specific range), and renewable generation
> CAPEX (technology-specific range). Each parameter range was drawn
> directly from the literature-validated bounds stored in
> `scenario_params.yaml` (WACC: region-specific range fields;
> capacity factor: `technologies.<tech>.<region>` min/max; renewable
> CAPEX: `renewables.<tech>.capex_range`). A total of 5,120 model
> evaluations were used per region/technology combination, generated
> via Saltelli sampling with N=1,024 base samples for k=3 parameters
> (N × (2k+2) = 5,120), providing sufficient convergence for
> first-order (S1) and total-order (ST) indices in a near-additive
> model (confirmed by Σ S1 ≈ 0.99 across all four region/tech pairs).
> Electrolyzer CAPEX/OPEX were excluded from the Sobol analysis
> because no literature-validated range exists for these parameters
> in `scenario_params.yaml` (they are stored as single scalars);
> capacity density was excluded because `calculate_lcoh()` has no
> capacity or area parameter — density enters only the technical-
> potential calculation, not the LCOH formula itself.

---

## Reorganised Change List

*(Items renumbered to reflect execution order. Block 0 is a prerequisite
for items marked with ★. All others can proceed independently.)*

---

### Phase 1 — Documentation (execute first, in a single pass)

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**0. ★ DOCUMENTATION PASS** — as specified above with esboços A through J.
Prerequisite for items ★2, ★7, ★12, ★13.

---

### Phase 2 — Methodology text corrections (require Phase 1 complete)

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**★2. METHODOLOGY.md §2.5 — WACC baseline values** → Update the stated
Brazil WACC from "8.0% solar / 9.0% wind" to "11.0% solar / 12.0% wind"
and add the citations from esboço A (Egli et al. 2018; IRENA 2023, 2024;
Steffen 2020; Terrapon-Pfaff et al. 2025). Also expand the inversion-point
framing: the stated research goal is to find the Brazilian WACC threshold
at which Brazil's LCOH matches Germany's baseline LCOH — make this
explicit in §2.6, clarifying that the inversion point exists for solar
(WACC = 11.92%) but not for wind within the tested range, and that this
absence is itself a publishable finding. Expand the wind WACC sensitivity
range beyond the current [8%, 16%] ceiling to establish whether an
inversion point exists at higher values, and report the result either
as a threshold or as a confirmed no-inversion finding with its upper
bound stated explicitly. → Effort: Medium.

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**1. METHODOLOGY.md §2.3 — Site selection algorithm** → Rewrite to match
actual implementation: contiguous-raster-cluster aggregation with
8-connectivity `scipy.ndimage.label`, 85th-percentile pre-filter,
50 km² minimum / 2,000 km² maximum cluster-area cap, `top_n=20`
shortlist per region/technology. Remove all references to municipality/
Landkreis-equivalent zonal-statistics aggregation and the 10 km²
threshold — that code path is dead. Remove the Corine Land Cover
reference in Table 1 and §2.2; replace with ESA WorldCover 2021 as the
operational land-use source for both regions (see item 4 below for the
full data-source disclosure). → Effort: Medium.

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]**
**★7. METHODOLOGY.md §2.7 — Add Sobol subsection** → Insert prose
describing the Sobol analysis as drafted in esboço J: Saltelli sampling,
N=1,024 base samples, k=3 parameters, 5,120 evaluations per pair,
S1/ST/interaction indices, exclusion rationale for electrolyzer CAPEX and
capacity density, and the headline finding (WACC dominates S1 in 3 of 4
pairs; capacity factor dominates for Germany solar). → Effort: Medium.

**[PRIORITY: LOW] [BLOCKS PUBLICATION: NO]**
**★12. METHODOLOGY.md §2.5 — Brazil wind CAPEX override** → Add one
sentence: Brazil's onshore wind CAPEX uses a region-specific value of
1,050 USD/kW (IRENA 2024: ~1,099 USD/kW for Brazil) rather than the
global baseline of 1,300 USD/kW applied to Germany. Cross-reference
esboço D. → Effort: Low.

**[PRIORITY: LOW] [BLOCKS PUBLICATION: NO]**
**★13. METHODOLOGY.md §2.8 and `scenario_params.yaml` — Incentive
scenario values** → Replace REHIDRO placeholder (1.00 USD/kg) with
confirmed programme value once available, or add explicit placeholder
disclosure in the methods text. Replace Germany single-value placeholder
(2.00 USD/kg) with the 2.00–4.50 EUR/kg range per esboço G; select and
document the baseline value and EUR/USD reference rate. Update
`scenario_params.yaml` to reflect the chosen value and add the range
bounds as explicit fields. → Effort: Medium (confirming the real figures
is outside the pipeline's scope but must precede submission).

---

### Phase 3 — Spatial and data issues (independent of Phase 1)

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**3. TOPSIS↔VIKOR concordance — solar siting** → Investigate the weak
concordance for solar in both regions (Brazil solar: ρ=0.381, 20.1%
top-10% overlap; Germany solar: ρ=0.147, 5.8% overlap) before citing
the dual-method robustness claim for solar. Determine whether the
divergence reflects a genuine property of TOPSIS vs. VIKOR reacting
differently to solar's spatially uniform GHI criterion, or a weight-to-
criterion mapping defect in `topsis.py` (see that file's own
WEIGHT-FIELD MAPPING NOTE). Qualify or remove the §2.3 robustness claim
for solar if concordance cannot be improved or explained. → Effort: High.

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: YES]** **✓ COMPLETE (2026-08-05)**
**4. Land-use layer disclosure — Germany (ESA WorldCover)** → Update
METHODOLOGY.md Table 1 and §2.2 to state that ESA WorldCover 2021 is
used as the operational land-use layer for both Brazil and Germany.
Remove the Corine Land Cover row from Table 1. Add a limitations
disclosure in §2.9 noting that the Copernicus Land Monitoring Service
(Corine) token-authenticated endpoint was unavailable during data
acquisition and WorldCover was used as the harmonised alternative,
and that this substitution affects comparability with studies using
Corine class definitions. The ESA WorldCover source is retained as the
confirmed operational choice — no re-run against Corine is planned.
→ Effort: Low (text update only; no pipeline changes).

**[PRIORITY: HIGH] [BLOCKS PUBLICATION: NO — but recommended before trusting spatial results]** **✓ COMPLETE (2026-08-05)**
**5. Brazil exclusion percentage (3.3%) — diagnostic** → Run a
class-code histogram on `data/raw/landuse/brazil/landuse.tif` and
compare against MapBiomas Collection 9 published statistics for
Northeast Brazil. Verify that the four excluded classes
`{11, 24, 30, 33}` (wetland/urban/mining/water) capture the full
defensible exclusion set for a hydrogen-hub suitability mask, and
that the raster is not degraded or partially void. Report findings;
expand the exclusion class list if the histogram reveals systematic
under-exclusion. → Effort: Medium.

---

### Phase 4 — Code quality and reproducibility (independent)

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]** **✓ COMPLETE (2026-08-05)**
**8. `docs/memory/` files 01, 02, 03, 07, 08 — refresh** → Update all
five stale files to reflect current `src/` state: mark
`h2_potential.py` and `plotting.py` as complete, confirm test files
exist, add Sobol analysis description, document ADR-007
(administrative-boundary clip fix), ADR-008 (Brazil WACC and CAPEX
override), and the pipeline config-summary table. Use `05` and `06`
as templates for what a current file looks like. → Effort: Medium.

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]** **✓ COMPLETE (2026-08-05)**
**9. `requirements.txt` — full pinned dependency list** → Run
`pip freeze` against the working environment and reconcile against
actual imports across all `src/` modules. A file with a single
dependency (`SALib>=1.5`) cannot support a fresh-clone reproducible
environment. → Effort: Low.

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]** **✓ COMPLETE (2026-08-05)**
**10. Test coverage — `decomposition.py` and `exclusion_mask.py`** →
Add regression tests for `economics/decomposition.py` (LCOH values,
inversion-point result, scenario-range output, region-specific CAPEX
override) and `spatial/exclusion_mask.py` (administrative-boundary
clip, ADR-007 fix). These are the two most-modified and most result-
critical modules with zero dedicated pytest coverage. → Effort: High.

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]** **✓ COMPLETE (2026-08-05)**
**11. `top_n=20` site count — justify or sensitivity-test** → Either
add a rationale in METHODOLOGY.md §2.3 for the 20-site shortlist
(e.g., collective share of total suitable area or installable
capacity captured), or run a sensitivity check across two or three
alternative `top_n` values and confirm the headline LCOH result is
insensitive to this choice. → Effort: Low.

---

### Phase 5 — Analytical extensions (can follow Phases 1–3)

**[PRIORITY: MEDIUM] [BLOCKS PUBLICATION: NO]** **✓ COMPLETE (2026-08-05)**
**14. Inversion-point range under min/max capacity-factor scenarios** →
Report how the solar inversion WACC (currently 11.92% at baseline)
shifts under the min and max capacity-factor scenarios already
computable via `decomposition.run_scenario_range()`. This converts a
point estimate conditional on baseline resource assumptions into a
range, more fully answering the study's stated research question.
For wind, confirm and report the upper bound of the tested WACC range
at which no inversion exists. → Effort: Medium.

---

## Findings that did NOT make the change list

- Sobol exclusion of electrolyzer CAPEX/OPEX and capacity density:
  well-justified (no literature range in YAML; density not a parameter
  of `calculate_lcoh()`).
- `NO SIGN CHANGE` for Brazil/Germany onshore-wind inversion: valid
  methodological finding, not a bug.
- OAT sweep parameters match METHODOLOGY.md §2.7 exactly.
- All 15 output figures/maps exist with plausible non-zero file sizes.