# 07 — Risks and Limitations

Distinct from bugs: these are deliberate scope boundaries or acknowledged
weaknesses of the study design and current implementation state, recorded
per `METHODOLOGY.md` §2.9 and the current codebase state. Treat this file
as a checklist before treating any pipeline output as final or
publication-ready.

## Data source asymmetries

- **Administrative boundaries (GADM vs. IBGE):** Brazilian boundaries come
  from a national cartographic authority (IBGE); German boundaries come
  from GADM, an aggregated third-party product, chosen to preserve full
  acquisition automation over the granularity/currency of a national source
  such as BKG VG250. This may introduce imprecision in fine-grained
  administrative-unit delineation used in zonal aggregation
  ([04_spatial_methodology.md](04_spatial_methodology.md)), and should be
  weighed when interpreting cross-country comparisons of administrative-unit
  rankings.
- **Land-use taxonomies (MapBiomas vs. ESA WorldCover):** the binary
  exclusion mask is implemented as a simplification, not a fully
  harmonized multi-class comparison between MapBiomas Collection 9
  (Brazil) and ESA WorldCover 2021 (Germany, the confirmed operational
  source — see "CDSE token dependency" below and
  [METHODOLOGY.md](../../METHODOLOGY.md) §2.9, change_list.md item 4) —
  the two taxonomies classify land differently, and the binary mask
  deliberately does not attempt to reconcile that. Corine Land Cover was
  the originally planned Germany source but was never the operational
  one in any result this study reports; see "CDSE token dependency"
  below for why.

## Absence of hourly dispatch modelling

Capacity factors are drawn from long-term Global Solar Atlas / Global Wind
Atlas resource-atlas averages, not from an hourly dispatch simulation. The
pipeline therefore does not characterize intra-year variability, storage
sizing, or grid-curtailment dynamics — a deliberate scope boundary, since
the central claim of the study is a static resource-versus-finance
decomposition, not a dispatch or storage study. All hydrogen production is
assumed dedicated to electrolysis, with no competing grid off-take, no
transmission-limit rule, and no curtailment-capture pathway.

## Live network access (corrected)

Earlier revisions of this file (and of `SPRINT_LOG.md`) stated no session
had network access to the acquisition sources. That was true through the
sprint's first several sessions but is no longer accurate: a later
session confirmed live reachability of Overpass, EEA discomap ArcGIS REST
(Natura 2000), and the public AWS `esa-worldcover` S3 bucket, and used it
to find and fix a real CRS regression in `admin_boundaries.py` (see
`SPRINT_LOG.md` Issue 5) that had silently broken every Germany fetcher
downstream of it. `services.terrascope.be` (a WMS viewer service tried as
one candidate ESA WorldCover source) resets every HTTPS request from this
network path regardless of request shape — confirmed independent of this
codebase via raw `curl` — so it was not used in the final implementation.
Always check `SPRINT_LOG.md`'s most recent session entry for the current,
authoritative state of what has and hasn't been verified live, rather
than trusting a network-access claim in this file at face value.

## CDSE token dependency

Corine Land Cover access (Germany land-use layer) requires a one-time
manual CDSE (Copernicus Data Space Ecosystem) credential setup — see
[03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md).
`acquisition/credentials.py` fails explicitly with a descriptive error if
`CDSE_TOKEN` is unset anywhere it checks (`.env`, environment variables,
or `config/cdse_credentials.json`); `acquisition/landuse_fetch.py:
fetch_germany()` is now implemented and verified to fail this way before
any network call when the credential is absent entirely. With a
placeholder (non-empty but invalid) token, the real Corine WCS endpoint
was reached live and returned HTTP 498 (invalid token), correctly
triggering a new ESA WorldCover 2021 fallback (public, unauthenticated,
via AWS-hosted COG tiles) rather than failing the whole fetch — so an
environment without a real CDSE token can still reproduce a (lower-
fidelity, non-Corine) Germany land-use layer and exclusion mask. Brazil's
MapBiomas path needs no credential and was not affected by this change.
A real CDSE token is still required to reproduce the actual Corine-based
result the methodology describes.

## Test coverage gaps (refreshed 2026-08-05, change_list.md item 8)

Every module `ARCHITECTURE.md` describes now exists and is complete —
`potential/h2_potential.py` (243 lines) and `viz/plotting.py` (450 lines)
are **no longer empty**; that was true only in an earlier sprint state.
`src/acquisition/` is 100% complete. `sensitivity/sensitivity_analysis.py`
is also complete: the economic one-at-a-time sweep (`run_sensitivity()`),
the TOPSIS weight-perturbation vs. VIKOR concordance table
(`run_mcda_sensitivity()`), and the Sobol global sensitivity analysis
(`run_sobol_analysis()`) are all implemented — none of these are still
"missing" as an earlier revision of this file stated. `rasterstats` (used
by `site_selection.py`'s currently-unexercised per-administrative-unit
branch) is now confirmed **installed** in this environment (version
0.21.0, re-verified 2026-08-05) — an earlier revision of this file and of
`SPRINT_LOG.md` stated it was not; that was accurate for the session that
wrote it, not for the current one. The remaining real gaps are narrower
and specific:

- **Zero pytest coverage for five result-critical modules**:
  `economics/decomposition.py`, `spatial/exclusion_mask.py`,
  `spatial/site_selection.py`, `spatial/vikor.py`, and
  `sensitivity/sensitivity_analysis.py` have no dedicated test file in
  `tests/` (only `test_lcoh_model.py`, `test_topsis.py`,
  `test_grid_utils.py`, and `test_admin_boundaries.py` exist — see
  [08_commands_and_reproducibility.md](08_commands_and_reproducibility.md)
  for the current 65-test breakdown). `decomposition.py` and
  `exclusion_mask.py` specifically are flagged as the two most-modified
  and most result-critical modules with zero coverage
  (change_list.md item 10, medium priority, not yet executed). All five
  modules have been exercised via ad hoc `python -c` verification and/or
  synthetic-fixture checks recorded in `SPRINT_LOG.md`, but none of that
  is captured as reusable, CI-runnable pytest.
- **`landuse_fetch.py`, `grid_infrastructure_fetch.py`, and
  `protected_areas_fetch.py`'s Brazil paths** are not yet verified against
  a live response in the environment they were written in (Germany's own
  paths for all three were verified live in an earlier session — see
  [03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md)
  for the per-module "Point to validate" notes).
- **`requirements.txt` is still nearly empty** — it contains only
  `SALib>=1.5` (a single pinned dependency), not the ~12 other real
  dependencies documented only in prose (`rasterio`, `pyproj`,
  `geopandas`, `rasterstats`, `numpy`, `scipy`, `pydantic`, `matplotlib`,
  `pandas`, `requests`, `python-dotenv`, `pytest` — see root `CLAUDE.md`
  and `ARCHITECTURE.md`'s dependency table). A fresh environment cannot
  `pip install -r requirements.txt` and get a working environment today.
  Flagged for a fix in change_list.md item 9 (not yet executed).

## TOPSIS↔VIKOR concordance is weak for solar (change_list.md item 3)

The VIKOR robustness cross-check (§2.3) confirms ranking stability for
onshore wind (Spearman ρ = 0.91 Brazil, 0.80 Germany) but shows
materially weaker concordance for solar (ρ = 0.38 Brazil, 0.15 Germany).
This is a diagnosed, genuine algorithmic property — not a weight-mapping
defect and not fixed — arising because TOPSIS's vector normalization is
sensitive to a criterion's raw coefficient of variation while VIKOR's
min-max normalization is not: solar's near-uniform GHI ends up
contributing almost no discriminating signal to TOPSIS's ranking despite
carrying the largest nominal weight, while VIKOR continues to weight it
near its full nominal share. The solar site ranking in this study is
therefore derived from TOPSIS alone; VIKOR is retained as a wind-specific
robustness confirmation only. Full diagnostic:
[04_spatial_methodology.md](04_spatial_methodology.md#topsisvikor-concordance-is-weak-for-solar-strong-for-wind--diagnosed-change_listmd-item-3).

## Brazil land-use exclusion diagnostic — confirmed correct (change_list.md item 5)

The 3.3% exclusion rate `exclusion_mask.py` reports for Brazil
(1,500,567 km² suitable / 1,552,206 km² total) was checked against a
direct class-code histogram of the real, on-disk
`data/raw/landuse/brazil/landuse.tif` and found consistent with expected
MapBiomas Collection 9 Northeast statistics for all four excluded classes
(urban, water, wetland, mining) — no systematic under-exclusion was
found, and no code change was made. Full diagnostic, including the
denominator correction needed to compare percentages meaningfully against
a raster whose rectangular bounding box includes substantial
out-of-region padding: [04_spatial_methodology.md](04_spatial_methodology.md#brazils-33-exclusion--verified-defensible-not-under-exclusion-change_listmd-item-5).

## WACC as a simplification

Even differentiated by technology (solar vs. wind), WACC remains a
simplification of a genuinely heterogeneous and partly confidential
financing landscape (Steffen, 2020). The evidentiary basis for the
Brazilian solar/wind WACC split is weaker in the reviewed literature than
for the German split.

## Placeholder economic values

REHIDRO (Brazil) and IPCEI Hy2Use (Germany) incentive values in
`config/scenario_params.yaml` are explicitly flagged placeholders, not
sourced from confirmed program documentation or a confirmed EUR/USD rate —
see [05_economic_model.md](05_economic_model.md). Do not use either in
publishable results without confirming the source figures.

## Scope boundary: production-stage LCOH only

The LCOH computed throughout covers production only, at the point of
electrolysis. Transport, compression, liquefaction, storage, and downstream
conversion (e.g. to ammonia) are excluded from the cost boundary. Study
conclusions should not be extended to delivered, transported, or converted
hydrogen cost without further work.

## Qualitative inversion-point interpretation

The comparison of inversion-point WACC thresholds against real-world
de-risking policy ranges is a qualitative, narrative interpretation
performed during manuscript preparation — not a formally modelled policy
scenario or an automated step of the computational pipeline.
