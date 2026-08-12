# 08 — Commands and Reproducibility

## Running the pipeline

`run_pipeline.py` is the full CLI orchestrator. It contains no analytical
logic — every stage function is a thin wrapper calling already-implemented,
already-verified public functions from `src/`. Config
(load/validate `scenario_params.yaml`) is not itself a `--stage` choice;
it always runs first, implicitly, since every stage needs it. The six
named stages always run in this fixed dependency order regardless of the
order `--stage` flags are passed: `acquisition → spatial → potential →
economics → sensitivity → viz`.

```bash
# Run everything, both regions (default)
python run_pipeline.py --stage all

# economics + sensitivity + viz never need live-acquired data (pure
# config-driven math, or graceful-skip-on-missing-input by design) --
# always safe to run offline
python run_pipeline.py --stage economics --stage sensitivity --stage viz

# Full run without touching the network, from whatever is already
# cached in data/raw/ and data/boundaries/
python run_pipeline.py --stage all --skip-acquisition

# Restrict to a single region
python run_pipeline.py --stage spatial --region germany
```

`--region` accepts `brazil`, `germany`, or `all` (default `all`); `--stage`
is repeatable and accepts `acquisition`, `spatial`, `potential`,
`economics`, `sensitivity`, `viz`, or `all` (default `all`).
`--skip-acquisition` skips the acquisition stage even if requested,
without making any network call.

**Robustness by design:** `acquisition`, `spatial`, `potential`, and
`sensitivity`'s MCDA half each wrap their per-region/per-technology work
individually — a missing upstream file (expected whenever live
acquisition hasn't populated `data/raw/`, which is the case in every
environment this project has been developed in so far, per
`07_risks_and_limitations.md`) is reported as `"status": "skipped"` in
that stage's JSON summary, never an unhandled exception that aborts
sibling regions or the rest of the run. `economics`, `sensitivity`'s
economic-OAT half, and `viz` are pure config-driven math or already
self-defensive (`generate_all_plots()`) and are expected to always
succeed — confirmed live: `--stage economics --stage sensitivity --stage
viz` runs to completion in ~10s with zero live acquisition data on disk,
producing real `outputs/tables/{decomposition,inversion_points,
economic_sensitivity,incentive_scenarios}.csv` and 7 real
`outputs/figures/*.png`. `--stage spatial --region germany` was also run
live against the real (partial) Global Solar/Wind Atlas + OSM data
already on disk for Germany (see `SPRINT_LOG.md`): 4 of 5 `data_layers`
loaders and `admin_boundaries` succeeded with genuine reprojection, while
`slope` and `exclusion_mask` correctly reported `"skipped"` (no SRTM/
land-use/protected-areas data acquired yet) — no network call, no crash.

## Running a single analytical module directly

Several economics/sensitivity modules expose their own `__main__` entry
point, independent of `run_pipeline.py`:

```bash
python -m src.economics.decomposition
python -m src.sensitivity.sensitivity_analysis
```

Both import `load_scenario_config` from `config.config_loader` — run them
from the project root so this import resolves. `viz/plotting.py` has no
`__main__` block (it is a pure library of plotting functions with no
default "generate everything" CLI entry point of its own); call
`generate_all_plots(config)` directly, after `outputs/tables/` and
`data/processed/` already contain the CSVs/rasters it reads:

```bash
python -c "
from config.config_loader import load_scenario_config
from src.viz.plotting import generate_all_plots
cfg = load_scenario_config()
print(generate_all_plots(cfg))
"
```

## Quick smoke test (`test_quick.py`)

`test_quick.py` performs two passes: (1) recursively imports every module
under `src/` and reports import successes/failures, then (2) runs targeted
functional sanity checks against the currently working modules
(`Region`/`RegionCRS`, `get_analysis_grid`, `get_grid_crs`, and others as
they become available). Run from the project root:

```bash
python test_quick.py
```

Use this first when picking up work after a break, or after any change to
`src/core/constants.py`, `config/config_loader.py`, or
`spatial/grid_utils.py` — it is the fastest way to catch an import-time
regression across the whole `src/` tree before running the slower pytest
suite.

## pytest suite

```bash
pytest tests/ -v
```

**65 tests across 4 files, 0 failures, fully offline** (no network, no
live-acquired data required):

- `tests/test_lcoh_model.py` (20 tests) — `calculate_lcoh()` /
  `calculate_lcoe()` against an independently-written reference
  implementation (not copy-pasted from the source, so a shared bug would
  have to be coincidental), the zero-WACC closed-form case, stack-
  replacement-excluded-in-final-year, PEM-vs-alkaline parameter effects,
  zero-incentive baseline equivalence, and every documented `ValueError`
  edge case.
- `tests/test_topsis.py` (16 tests) — `_vectorized_topsis()` boundary
  conditions (dominant row exactly `C=1`, dominated exactly `C=0`) and
  monotonicity, `perturb_topsis_weights()` exact-target/renormalization/
  clamping behavior, and `_get_default_weights()` cross-checked against
  the real loaded `scenario_params.yaml` (the WEIGHT-FIELD MAPPING NOTE,
  verified as code, not just docstring prose).
- `tests/test_grid_utils.py` (17 tests, **new this session**) —
  `get_analysis_grid()` dimensions/CRS-mismatch validation with explicit
  `bounds=` (no network), square-pixel/no-AEA-distortion confirmation, and
  `reproject_and_resample()`/`align_rasters()` against synthetic GeoTIFF
  fixtures built under `tmp_path` — including a genuine reprojection
  between the two real study-region AEA CRS definitions (not a trivial
  identity transform). An autouse fixture cleans up the
  `data/processed/*_reprojected.tif` side-effect files these functions
  unconditionally persist to the real project directory (Hard Rule 7),
  so the suite never leaves stray fixtures behind in real project state.
- `tests/test_admin_boundaries.py` (12 tests, **new this session**) —
  `get_dissolved_polygon()` / `get_region_bounds()` against synthetic
  GeoJSON fixtures (monkeypatching `_BOUNDARY_FILES`, never reading this
  project's real `data/boundaries/*.geojson`, so results don't depend on
  what happens to already be on disk): CRS-tag-override behavior, dissolve
  of multiple polygons, both directions of the sanity-extent check,
  missing/unparseable-file errors, and the module-level cache (including
  a `Region` str-Enum quirk this session discovered while writing the
  "unrecognized region" test — `"brazil" == Region.NORDESTE_BR` is `True`
  because `Region(str, Enum)` compares equal to its own value string, so
  that test uses a string matching no member's value at all).

### Test coverage status table (re-verified live, 2026-08-05)

| Test file | Tests | Module(s) covered |
|---|---|---|
| `test_lcoh_model.py` | 20 | `economics/lcoh_model.py` |
| `test_topsis.py` | 16 | `spatial/topsis.py` |
| `test_grid_utils.py` | 17 | `spatial/grid_utils.py` |
| `test_admin_boundaries.py` | 12 | `spatial/admin_boundaries.py` |
| **Total** | **65** | 4 of the ~13 non-acquisition `src/` modules |

Counts confirmed via `pytest tests/ --collect-only -q` (65 collected) and
`pytest tests/ -q` (65 passed, 0 failed), both re-run live this session.

**Zero dedicated pytest coverage**: `economics/decomposition.py`,
`spatial/exclusion_mask.py`, `spatial/site_selection.py`,
`spatial/vikor.py`, and `sensitivity/sensitivity_analysis.py` have no
test file in `tests/` — see
[07_risks_and_limitations.md](07_risks_and_limitations.md)'s "Test
coverage gaps" section and change_list.md item 10 (medium priority, not
yet executed), which specifically names `decomposition.py` and
`exclusion_mask.py` as the two most-modified, most result-critical
modules with this gap.

**Sobol analysis validation**: `sensitivity_analysis.run_sobol_analysis()`
is validated by the Σ S1 ≈ 1.0 convergence check, per Saltelli et al.
(2008)'s recommended diagnostic for a near-additive model — confirmed
live at Σ S1 ≈ 0.99 across all 4 region/technology pairs (see
[05_economic_model.md](05_economic_model.md#sobol-global-sensitivity-analysis-srcsensitivity)
for the full results and ADR-006 in
[06_technical_decisions_log.md](06_technical_decisions_log.md) for why
only 3 of the originally proposed 5 parameters are covered). This is a
model-diagnostic check, not itself a pytest test — it has the same "zero
dedicated coverage" status as the rest of `sensitivity_analysis.py` above.

## Stage-by-stage verification commands

These are the concrete commands used to verify each module as it was
completed (from `SPRINT_LOG.md`); reuse them after any change to the same
module rather than inventing new ad hoc checks.

**`admin_boundaries.py` — confirm zero-network, disk-only reads:**

```bash
python -c "
from src.spatial.admin_boundaries import get_region_bounds
from src.core.constants import Region
for r in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
    b = get_region_bounds(r)
    print(f'{r.value}: {b}')
print('No network calls, reads from disk')
"
```

**`decomposition.py` — confirm the baseline is structurally incentive-free:**

```bash
python -c "
from src.economics import decomposition
import inspect
src = inspect.getsource(decomposition._lcoh_for)
assert 'region_cfg.incentives' not in src
assert 'incentive_value_usd_per_kg=0.0' in src
print('Baseline is incentive-free')
"
```

**`data_layers.py` — confirm loader surface:**

```bash
python -c "
from src.spatial import data_layers
assert hasattr(data_layers, 'load_distance_to_water')
assert not hasattr(data_layers, '_get_target_crs')
print('OK:', [n for n in dir(data_layers) if n.startswith('load_')])
"
```

**`water_bodies_fetch.py` — live network call against Overpass API.**
Germany's bbox takes ~3–4 minutes; Brazil's 9-state bbox is expected to
take substantially longer and has not yet been run live (see
[07_risks_and_limitations.md](07_risks_and_limitations.md)):

```bash
python -c "
from config.config_loader import load_scenario_config
from src.core.constants import Region
from src.acquisition.water_bodies_fetch import fetch
from src.spatial.data_layers import load_distance_to_water

cfg = load_scenario_config()
path = fetch(Region.NORTH_GERMANY, cfg)
array, meta = load_distance_to_water(Region.NORTH_GERMANY, cfg)
print(f'wrote {path}, shape={array.shape}, '
      f'mean={array.mean():.0f}m, max={array.max():.0f}m, units={meta[\"units\"]}')
"
```

**`incentive_scenarios.py` — confirm incentive LCOH undercuts baseline
for both regions, wrong-region calls raise, and the result is persisted
to `outputs/tables/incentive_scenarios.csv` (added this session, closing
the gap `viz/plotting.py`'s session flagged in
[05_economic_model.md](05_economic_model.md)):**

```bash
python -c "
import pandas as pd
from config.config_loader import load_scenario_config
from src.economics.incentive_scenarios import run_all_incentive_scenarios, INCENTIVE_SCENARIOS_CSV
cfg = load_scenario_config()
r = run_all_incentive_scenarios(cfg)
assert r['brazil']['rehidro_lcoh_usd_per_kg'] < r['brazil']['baseline_lcoh_usd_per_kg']
assert r['germany']['ipcei_lcoh_usd_per_kg'] < r['germany']['baseline_lcoh_usd_per_kg']
df = pd.read_csv(INCENTIVE_SCENARIOS_CSV)
assert set(df['region']) == {'brazil', 'germany'}
print('Incentive scenarios lower LCOH vs baseline for both regions, CSV persisted:', r)
"
```

**`plotting.py` — confirm headless backend and produce the economic
figures from real, already-computed CSVs (no network/raster data needed):**

```bash
python -c "
import matplotlib
from config.config_loader import load_scenario_config
from src.economics.decomposition import run_all_decompositions
from src.viz.plotting import generate_all_plots
assert matplotlib.get_backend() == 'Agg'
cfg = load_scenario_config()
run_all_decompositions(cfg)  # writes outputs/tables/decomposition.csv + inversion_points.csv
results = generate_all_plots(cfg)
print('Agg backend confirmed; figures written:', results['figures'])
"
```

Suitability/candidate-sites maps require real
`topsis_suitability_*.tif` / `exclusion_mask_*.tif` /
`candidate_sites_*.geojson` on disk (from the spatial pipeline) — not yet
run live in this environment (no network access for the upstream
acquisition fetches); `plot_suitability_map()` /
`plot_candidate_sites_map()` were verified instead against synthetic
GeoTIFF/GeoJSON fixtures, the same pattern used to verify the spatial
modules themselves (see `SPRINT_LOG.md`).

**`run_pipeline.py` — full orchestrator, run live this session (task's
own verification command):**

```bash
python run_pipeline.py --stage economics --stage sensitivity --stage viz
```

Ran to completion in ~10s: `economics` and `sensitivity`'s economic-OAT
half report `"status": "ok"`; `sensitivity`'s 4 MCDA
region/technology pairs each report `"status": "skipped"` with an
explicit "Raw land-use raster not found" error (expected -- no live
acquisition in this environment); `viz` reports `"status": "ok"` with 0
maps (no spatial rasters on disk) and 7 figures. A broader
`python run_pipeline.py --stage all --skip-acquisition` run additionally
exercised `spatial` and `potential` the same way — every stage completes,
every missing-input case is a reported skip, nothing crashes.

## Before trusting any of the above

Cross-check `SPRINT_LOG.md`'s "Module Status Snapshot" and "Test Status"
sections for the current, authoritative state — this file records the
commands, not a live status; module status changes faster than this
directory is guaranteed to be updated. See the validation guidance in
[README.md](README.md).
