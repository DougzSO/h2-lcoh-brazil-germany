# 04 — Spatial Methodology

## 1 km² equal-area common grid

All raster layers (solar, wind, slope, distance-to-grid, distance-to-water,
land use, protected areas) are reprojected and resampled onto a common
1 km² analysis grid, built per region by `spatial/grid_utils.py:
get_analysis_grid()`. Grid width/height are computed as `ceil((maxx - minx)
/ 1000)` / `ceil((maxy - miny) / 1000)`, which means actual cell size is
`(maxx - minx) / width` and not always exactly 1000.0 m — see the distance
rasters note in
[03_data_sources_and_acquisition.md](03_data_sources_and_acquisition.md).
Because the grid is equal-area (see below), raster cell counts translate
directly into square kilometres without a separate geodetic
area-correction step, which is what the technical-potential and zonal
site-aggregation computations rely on.

## CRS definitions

> ⚠️ Point to validate against `src/core/constants.py` before use: **the
> implementation does not use EPSG:31984 (Brazil) / EPSG:25832 (Germany).**
> `RegionCRS` in `src/core/constants.py` defines two **custom Albers Equal
> Area Conic (AEA) projections**, one per region, as PROJ4 strings:
>
> - Northeast Brazil: `+proj=aea +lat_1=-5 +lat_2=-15 +lat_0=-10 +lon_0=-40
>   +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs`
> - North Germany: `+proj=aea +lat_1=53 +lat_2=54.5 +lat_0=53.75 +lon_0=9.5
>   +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs`
>
> The module docstring records the rationale explicitly: UTM (the family
> EPSG:31984 and EPSG:25832 both belong to) is conformal — it preserves
> shape/angle, not area. At the scale of these study regions (NE Brazil
> spans ~1,500 km across multiple UTM zones; North Germany spans two full
> Bundesländer), UTM's area distortion away from a zone's central meridian
> is not negligible and would silently bias every area-dependent
> computation in the pipeline: zonal statistics, installable capacity (=
> suitable area × power density), and per-site LCOH. A custom AEA per
> region guarantees area fidelity everywhere within that region's extent,
> at the cost of angular distortion the pipeline never relies on. If a
> future change re-introduces EPSG:31984/25832, it must be a deliberate,
> documented trade-off against this rationale — not a silent drift.

The geographic (degree-based) CRS for source data is `EPSG:4326`
(`RegionCRS.GEOGRAPHIC_WGS84`).

**Hard rule:** CRS strings are never hardcoded outside `src/core/constants.py`.
Every call site resolves CRS via `RegionCRS.projected_crs_for(region)`, and
`spatial/grid_utils.get_analysis_grid()` cross-validates that
`config.regions.<region>.grid_crs` (from `scenario_params.yaml`) matches
`RegionCRS.projected_crs_for(region)` **exactly** (string equality on the
PROJ4 string), raising `ValueError` on any drift — a mismatch here would
silently misalign the analysis grid against boundary geometry.

## Binary exclusion masking

Protected areas, water bodies, and built-up/urban land are treated as
**hard exclusion constraints via a binary mask**, not as weighted TOPSIS
criteria — no defensible development can occur in these areas regardless
of resource quality. Implemented in `spatial/exclusion_mask.py`:
`create_exclusion_mask(region, config) -> Tuple[np.ndarray, Dict]`
combines three inputs via logical AND and writes
`data/processed/exclusion_mask_{region}.tif` (uint8, 1=suitable,
0=excluded):

- **Land-use reclassification**: `landuse_fetch.py`'s raw categorical
  raster is warped directly onto `get_analysis_grid()`'s exact
  transform/shape via `rasterio.warp.reproject` with nearest-neighbor
  resampling (never `grid_utils.reproject_and_resample()`, which computes
  its own output transform from the source's reprojected extent and is
  not guaranteed to land on identical pixels — see the ALIGNMENT NOTE in
  the module docstring), then reclassified into suitable/excluded.
  MapBiomas classes `{11, 24, 30, 33}` (wetland, urban, mining, water) and
  Corine ranges `111–142` / `411–423` / `511–523` (artificial surfaces,
  wetlands, water) are excluded per region; class code `0` is always
  excluded as nodata/unclassified; everything else is treated as
  suitable — the exclusion list is authoritative, not an enumerated
  suitable-class list.
- **Protected-area rasterization**: `protected_areas_fetch.py`'s GeoJSON
  output is reprojected and rasterized (`rasterio.features.rasterize`)
  onto the *same* reference grid, inverted so 1=unprotected/0=protected.
- **Administrative-boundary rasterization** (`_rasterize_boundary()`):
  `admin_boundaries.get_dissolved_polygon(region)` rasterized onto the same
  reference grid, 1=inside the boundary/0=outside. **Fixed a real gap**:
  `get_analysis_grid()` sizes the grid to the region's rectangular
  *bounding box* (`get_region_bounds()`), not its actual outline — for an
  elongated shape like Northeast Brazil's 9-state coastline, the bbox is
  2.23x the polygon's true area (verified live: 3,461,794 km² bbox vs.
  1,552,166 km² dissolved polygon). Before this fix, nothing checked
  polygon containment: land-use/protected-area classification ran over the
  *entire* bbox, including cells geographically outside the region
  (ocean, neighboring states/Bundesländer), and `total_km2` in this
  function's own metadata was `grid.size * pixel_area` (the bbox area),
  not the region's real area. Both are now correct: Brazil's exclusion
  mask reports `total_km2 ≈ 1,552,206` (matches the canonical dissolved
  area to <0.01%) and `suitable_km2 ≈ 1,500,568`, down from the old
  bbox-inflated `2,233,455` suitable / `3,461,794` total. Germany's own
  bbox-vs-polygon gap was smaller in absolute terms but still real
  (`139,086` bbox km² → `63,298` true dissolved km²), fixed identically
  since this is shared, region-agnostic code. Because every downstream
  raster (TOPSIS/VIKOR criteria, `site_selection.py`'s clustering) keys
  off this mask, the fix propagates automatically — no changes were made
  to `topsis.py`, `vikor.py`, or `site_selection.py` themselves.

Because all three inputs are built against `get_analysis_grid()`'s
identical transform/shape, they are guaranteed pixel-aligned before the
logical AND — no separate `align_rasters()`-style shape check was needed,
though the module still asserts shape equality defensively before
combining.

### Brazil's 3.3% exclusion — verified defensible, not under-exclusion (change_list.md item 5)

`exclusion_mask.py`'s own RECLASSIFICATION NOTE flags that its MapBiomas
class codes were "taken from published legend documentation, not verified
against a live-fetched raster's actual class distribution." This is now
resolved for Brazil: a standalone diagnostic
(`scripts/diagnostic_brazil_landuse_histogram.py`, read-only, does not
import or modify `exclusion_mask.py`) histogrammed the real, on-disk
`data/raw/landuse/brazil/landuse.tif` (EPSG:4326, ~204 m native pixels,
83,878,235 px) by class code, with per-row latitude-corrected km²
conversion (a flat pixel-area factor would be ~5% off across this
raster's −18.34° to −1.05° latitude span).

**Finding: the 3.3% figure is correct and does not indicate
under-exclusion.** The raw raster's own class composition, restricted to
its classified (non-nodata) area (2,343,732 km² — nodata is 31.89% of the
raster's rectangular bbox, almost entirely the padding outside the true
9-state dissolved boundary already documented above, not a data gap
within Brazil's actual territory), matches expectation for all four
excluded classes: urban (24) 0.565%, water (33) 1.360%, wetland (11)
0.608%, mining (30) 0.024% — all at or above the sanity-check minimums
used for this diagnostic (urban >0.5%, water >1%), and wetland/mining
both clearly non-zero and present. (Measuring these same percentages
against the raw bbox's full, nodata-inclusive area instead — 3,441,313
km² — understates each by the same ~1.47x factor and would have
incorrectly suggested urban/water were below expectation; the
classified-land denominator is the correct basis for comparison against
published per-territory statistics, since the bbox's nodata padding is
not part of Northeast Brazil's actual land area.) Cross-checked against
`exclusion_mask.py`'s own final reported metric (1,500,567 km² suitable /
1,552,206 km² total, 3.3% excluded, computed on the 1 km analysis grid
after AND-ing with the true dissolved boundary): the four excluded
classes' raw-raster area (59,916 km², summed over the padded bbox) and
the final excluded area (51,639 km², restricted to the true boundary) are
within 14% of each other — the expected order of magnitude given most of
the raw exclusion area sits inside the true state boundaries (not the
bbox's ocean/border padding), with the remaining gap attributable to
204 m→1 km nearest-neighbor resampling and exact boundary clipping, not a
missing exclusion class.

**No other high-coverage class warrants addition to the exclusion set.**
The largest non-excluded classes are Savanna Formation (28.1% of the raw
bbox), Pasture (18.6%), and Forest Formation (9.5%) — all legitimately
developable or already-suitable land-cover types for renewable siting,
not exclusion candidates. The task's own flagged concern ("dense forest
in protected areas is sometimes coded separately from the protected-area
vector layer") does not apply to this pipeline's design: protection
status is determined exclusively by `_rasterize_protected_areas()`
against the real ICMBio/MMA protected-area polygons, ANDed in
independently of land-use class — adding Forest Formation to
`MAPBIOMAS_EXCLUDED_CLASSES` would incorrectly exclude the large majority
of Northeast Brazil's legitimately unprotected forest and savanna, not
just the portion that happens to fall inside an actual protected area
(which the protected-area mask already removes on its own).

**Action: no change to `exclusion_mask.py` or `scenario_params.yaml`.**
`MAPBIOMAS_EXCLUDED_CLASSES = {11, 24, 30, 33}` is confirmed sufficient;
the module's own RECLASSIFICATION NOTE can be treated as resolved for
Brazil (Germany's Corine/WorldCover class-code set remains separately
flagged and was explicitly out of scope for this diagnostic).

**RESOLVED (ADR-010, `06_technical_decisions_log.md`):** the design
question this section used to flag has been resolved. `max_slope_degrees`
and `min_capacity_factor` were confirmed unused anywhere in `src/` (not
binary cutoffs, not TOPSIS-weighted via config) and removed from
`ThresholdsConfig`/`scenario_params.yaml` entirely — slope remains
purely a weighted TOPSIS criterion (`METHODOLOGY.md` §2.3), never a binary
exclusion. `max_distance_to_grid_km` was likewise confirmed unused
anywhere in `src/` (including `topsis.py`'s `proximity_infrastructure`
criterion — it is not read from config there either) and removed too.
`min_distance_to_protected_area_km` is the one survivor, and it is no
longer dead configuration: `exclusion_mask._rasterize_protected_areas()`
now buffers protected-area polygons by this distance (converted to
metres) before rasterizing, closing the gap this section used to flag.
**Since ADR-011**, this field is a per-region value
(`ProtectedAreaBufferConfig`: `brazil`/`germany`), not a shared scalar —
see "Literature basis for spatial thresholds" below.

### Literature basis for spatial thresholds

**Slope threshold (15°, weighted TOPSIS criterion, not a binary cut):**
areas with steeper terrain slope score lower in TOPSIS's weighted
suitability criterion (`METHODOLOGY.md` §2.3) — never a hard exclusion
cutoff at 15° or any other value; `max_slope_degrees` was removed from
`scenario_params.yaml`'s `thresholds` block as unused dead configuration
(see RESOLVED note above). 15° itself remains a literature reference point
(intermediate between the more restrictive 10° limit applied in some
solar-siting studies and the 20° limit used in wind-siting studies —
Masurowski et al., 2017; Eberle et al., 2019) informing how the slope
criterion is understood in the manuscript, not a value read by any code
path today.

**Protected area buffer (`min_distance_to_protected_area_km`, ACTIVE,
REGION-SPECIFIC since ADR-011):** Brazil retains a 2 km exclusion buffer
around protected-area boundaries, exceeding the minimum setback
requirements documented in most Brazilian environmental licensing
frameworks and consistent with a precautionary approach around the
region's federal conservation units (UCs federais), which are strict
no-use reserves. Germany uses 500 m instead: its protected-areas dataset
is Natura 2000, which permits sustainable renewable-energy development
under EU law subject to appropriate mitigation, and a uniform 2 km buffer
was found to exclude ~60% of the German study region (verified live
before this change: 25k km² suitable at 2 km buffer vs. 63,298 km² total
region area) — inconsistent with actual German RE siting practice (VDMA/
BNetzA data document wind/solar deployment inside Natura 2000 with
appropriate mitigation). `exclusion_mask._rasterize_protected_areas()`
applies each region's own buffer (via `geopandas.GeoSeries.buffer()`, in
the region's own projected CRS, already in metres), resolved per region
by `_config_helpers.get_protected_area_buffer_km()` (the same
`Region` → config-key translation pattern as `get_grid_crs()`) before
rasterizing, so a cell within the buffer distance of a protected area's
boundary is excluded even though it falls outside the polygon itself.
**Verified live after the region split:** Brazil 1,484,236 km² suitable /
1,552,206 km² total (4.4% excluded, unchanged from the uniform-2km
baseline, since Brazil's own buffer value didn't change); Germany 45,113
km² suitable / 63,298 km² total (28.7% excluded, up from ~25k km²
suitable / ~60% excluded at the old uniform 2 km buffer) — within the
45–55k km² range expected for a setback consistent with actual German RE
siting practice.

**Distance to grid (50 km, TOPSIS-weighted, exclusion role unconfirmed):**
grid connection distance is scored continuously in the TOPSIS
`proximity_infrastructure`/distance-to-grid criterion (Masurowski et al.,
2017, cites 50 km as a practical upper limit for economically viable grid
extension in technical-potential assessments). `max_distance_to_grid_km`
was removed from `scenario_params.yaml` as unused dead configuration (see
RESOLVED note above) — confirmed not read by `topsis.py`'s own
`proximity_infrastructure` criterion either, so today no code path applies
a 50 km cutoff of any kind; the 50 km literature reference remains
documentary context only.

**Cluster area bounds (`min_contiguous_suitable_area_km2` = 50 km²,
`max_contiguous_suitable_area_km2` = 2,000 km²):** a minimum contiguous
suitable area of 50 km² filters out isolated cells and small fragments
unsuitable for a GW-scale hydrogen hub footprint. A maximum of 2,000 km²
prevents spatially extended uniform corridors (coastal strips, large
agricultural plains) from being retained as a single hub-scale site;
clusters exceeding this limit are trimmed to their highest-suitability
2,000 km² subset by `site_selection.py` (see "Zonal site aggregation"
below and the bumped-threshold rationale already recorded inline in
`scenario_params.yaml`'s `thresholds` block).

Citations: Masurowski et al. (2017); Eberle et al. (2019). `METHODOLOGY.md`
§2.3 states the same threshold values (15°, 2 km Brazil / 500 m Germany
protected-area buffer, 50 km, 50–2,000 km²) with the same citation set.

## TOPSIS and VIKOR multicriteria formulations

**TOPSIS** (Technique for Order of Preference by Similarity to Ideal
Solution) is the primary method — distance-based, admits literature-derived
criterion weights directly, and avoids the pairwise expert elicitation used
in AHP. Applied independently per region **and per renewable technology**
(solar/wind — each gets its own resource criterion and its own output
raster) on the common 1 km grid. Implemented in `spatial/topsis.py`:
`run_topsis(region, tech, config, custom_weights=None) -> Tuple[np.ndarray, Dict]`.

TOPSIS is computed **only over cells not excluded by
`exclusion_mask.create_exclusion_mask()`** (called as-is, not modified) and
with finite values on all four criteria — never over excluded pixels, a
deliberate memory/correctness optimization, not just a performance one
(an excluded cell has no defensible suitability score at all). The
standard vector-normalized procedure is used: $r_{ij} = x_{ij} /
\sqrt{\sum_k x_{kj}^2}$, $v_{ij} = w_j r_{ij}$, ideal/anti-ideal points per
criterion direction (max/min swapped for cost vs. benefit criteria), and
$C_i = S_i^- / (S_i^+ + S_i^-)$. Output is written to
`data/processed/topsis_suitability_{region}_{tech}.tif` (float32);
excluded/invalid cells are set to exactly `0.0` in the output raster —
note this is the same value a genuinely valid but maximally-poor cell can
legitimately receive (`C_i = 0` is a real TOPSIS boundary case, not unique
to exclusion), so a `0.0` pixel alone does not distinguish "excluded" from
"scored worst"; cross-reference the exclusion mask raster directly if that
distinction matters downstream.

`spatial/topsis.py` also exposes `perturb_topsis_weights(weights,
target_criterion, delta_pct) -> Dict[str, float]`, used by
`sensitivity/sensitivity_analysis.py`'s weight-perturbation-vs-VIKOR
concordance check (§2.7): adjusts one criterion's weight by `delta_pct`
(e.g. ±0.20) and proportionately renormalizes the rest so the full set
still sums to 1.0.

> ⚠️ Point to validate: `topsis.py`'s four criteria (`resource`, `slope`,
> `distance_to_grid`, `distance_to_water`) do not literally match
> `config.topsis.weights`' four Pydantic field names
> (`resource_quality`, `proximity_infrastructure`, `land_availability`,
> `grid_distance`) — only `resource_quality`→`resource` and
> `grid_distance`→`distance_to_grid` are unambiguous by name.
> `topsis.py` maps `proximity_infrastructure`→`distance_to_water` (water
> is a distinct electrolysis-input "infrastructure" proximity concern from
> the transmission grid) and `land_availability`→`slope` (flatter terrain
> is more buildable/"available"), documented in the module's
> WEIGHT-FIELD MAPPING NOTE docstring. `scenario_params.yaml` itself was
> not changed to resolve this — confirm the mapping against the
> manuscript's intended criteria-to-weight correspondence before treating
> a TOPSIS ranking as final.

> ⚠️ Point to validate: `data_layers.py`'s five loaders (solar, wind,
> slope, distance-to-grid, distance-to-water) each reproject via
> `grid_utils.reproject_and_resample()`, which computes its own output
> transform per source raster — not guaranteed to align with
> `get_analysis_grid()`'s transform, nor with each other. `topsis.py`
> resolves this the same way `exclusion_mask.py` resolved it for land
> use: it re-reads each loader's `metadata["aligned_path"]` GeoTIFF and
> warps (bilinear) directly onto the reference grid itself, rather than
> trusting `data_layers.py`'s own output alignment. **`vikor.py` does not
> re-implement this a third time** — it imports `topsis._load_criterion()`
> (and `topsis.CRITERIA`, `topsis.CRITERION_DIRECTION`,
> `topsis._get_default_weights()`) directly, so both methods score
> identical, identically-aligned inputs. This alignment logic is still
> conceptually duplicated once, between `exclusion_mask.py` (nearest,
> categorical) and `topsis.py`/`vikor.py` (bilinear, continuous, now
> shared between the two) — a genuine third implementation was avoided.

**VIKOR** (Visekriterijumska Optimizacija I Kompromisno Resenje) is applied
as an independent robustness cross-check: the ranking of top-suitability
cells under VIKOR is compared against the TOPSIS ranking to confirm the
suitability result is not an artefact of the specific MCDA method chosen.
Implemented in `spatial/vikor.py`:
`run_vikor(region, tech, config, v=0.5, custom_weights=None) -> Tuple[np.ndarray, Dict]`,
scoring the **same four criteria, same exclusion mask, same valid-cell
restriction, and same weight resolution as `topsis.py`** (imported
directly, not re-implemented — see the Point to validate note above).

For each criterion $j$, $f_j^*$/$f_j^-$ are the best/worst values among
valid cells (max/min swapped for cost vs. benefit criteria, mirroring
TOPSIS's ideal/anti-ideal). Normalized distance from the ideal:
$d_{ij} = (f_j^* - x_{ij}) / (f_j^* - f_j^-)$. Group utility
$S_i = \sum_j w_j d_{ij}$; individual regret $R_i = \max_j(w_j d_{ij})$.
Compromise index $Q_i = v \frac{S_i - S^*}{S^- - S^*} + (1-v)
\frac{R_i - R^*}{R^- - R^*}$, with $v = 0.5$ (equal weight to group
utility and individual regret, per METHODOLOGY.md). $Q_i \in [0, 1]$,
0 = best, converted to the module's suitability scale via
$\text{Suitability}_i = 1 - Q_i$ so 1.0 = best, matching TOPSIS's
higher-is-better $[0, 1]$ convention — the property that makes the two
rasters directly comparable. Output is written to
`data/processed/vikor_suitability_{region}_{tech}.tif` (float32); the same
"`0.0` doesn't distinguish excluded from scored-worst" caveat noted above
for TOPSIS applies here too.

`spatial/vikor.py` also exposes
`compute_concordance(topsis_scores, vikor_scores, mask, top_k_pct=0.10) -> Dict[str, float]`,
the concrete implementation of the §2.3/2.7 robustness check: Spearman
rank correlation (`scipy.stats.spearmanr`) between the two rasters over
`mask == 1` cells, plus a Jaccard similarity
($|\text{intersection}| / |\text{union}|$, as a percentage) of the
top-`top_k_pct` cells ranked by each method — a high rank correlation with
low top-k overlap would indicate the methods agree broadly but diverge
exactly where site selection cares most.

### TOPSIS↔VIKOR concordance is weak for solar, strong for wind — diagnosed (change_list.md item 3)

**Observed (`outputs/tables/mcda_sensitivity_concordance_{region}_solar.csv`,
re-verified live 2026-08-05 directly against the on-disk
`topsis_suitability_*.tif`/`vikor_suitability_*.tif` rasters):** Brazil
solar ρ=0.3811 (Pearson r=0.4004), Germany solar ρ=0.1469 (Pearson
r=0.1473), both far below wind's Brazil ρ=0.9131 / Germany ρ=0.7998.
Top-10% cell overlap: Brazil solar 20.1%, Germany solar 5.8%, vs. Brazil
wind's much higher overlap under the same method. The score-difference
histogram (TOPSIS − VIKOR) is one-sided and unimodal for solar (mean
+0.30 to +0.35, essentially no cells with a negative difference), not
bimodal or randomly scattered — consistent with a systematic aggregation
divergence, not a data-alignment defect (a genuine misalignment bug would
be far more likely to produce scattered or sign-inconsistent errors, not
a smooth, one-sided offset).

**Ruled out — weight-mapping defect.** `vikor.py` imports
`topsis.CRITERIA`, `topsis.CRITERION_DIRECTION`,
`topsis._get_default_weights()`, and `topsis._load_criterion()` directly
(see the REUSE NOTE in `vikor.py`'s own module docstring) — both methods
score the exact same four criteria, the exact same weights, and the exact
same grid-aligned rasters for every region/tech pair. There is no
code path by which TOPSIS and VIKOR could see different inputs for the
same run. This was directly re-confirmed by inspection of both modules'
source this session, not just inferred from the docstring claim.

**Root cause — confirmed, not the mechanism the investigating task
initially proposed.** The initial hypothesis was that "VIKOR's
ideal/anti-ideal logic collapses when GHI is spatially uniform." Direct
measurement shows the opposite direction of effect: it is **TOPSIS's
vector normalization**, not VIKOR's min-max normalization, that loses
discriminating power on a low-variance criterion.

- TOPSIS normalizes each criterion by its own L2 norm across all valid
  cells (`r_ij = x_ij / sqrt(Σ x_kj²)`). This preserves — is directly
  proportional to — the criterion's raw coefficient of variation (CV).
  GHI (solar's resource criterion) is spatially near-uniform: CV=0.0152
  (Germany), CV=0.0598 (Brazil), against slope/distance-to-grid/
  distance-to-water's CVs of 0.77–1.66 (identical rasters for both
  technologies, since only the resource criterion differs between solar
  and wind runs). Measured directly on the weighted, vector-normalized
  matrix (`v_ij = w_j · r_ij`): resource contributes only **1.1%** of the
  total discriminating variance (`std(v_ij)`) for Germany solar, despite
  carrying the largest nominal weight (35%) — slope, distance-to-grid,
  and distance-to-water (nominal weights 20/20/25%) supply the other
  98.9%. For Germany wind, whose resource criterion (wind power density)
  has a much higher CV (0.2426), resource's share of discriminating
  variance rises to 15.1% — still below its nominal weight, but enough to
  meaningfully participate in the ranking.
- VIKOR normalizes each criterion by its own observed min/max
  (`d_ij = (f*_j − x_ij) / (f*_j − f-_j)`), which by construction always
  rescales every criterion to fill `[0, 1]` **regardless of its absolute
  CV** — a criterion that is nearly spatially uniform in raw terms still
  gets its full range of relative rank stretched out and multiplied by
  its full nominal weight. Confirmed directly: resource is the criterion
  that maximizes `w_j · d_ij` (VIKOR's individual-regret term `R_i`) in
  97.5–100% of valid cells for **both** solar and wind, in both regions —
  VIKOR never mutes resource's influence the way TOPSIS's normalization
  does for a low-CV criterion.
- Net effect: for solar, TOPSIS's ranking is driven almost entirely by
  slope/grid-distance/water-distance (resource is nominally the largest
  weight but contributes almost no discriminating signal), while VIKOR's
  ranking continues to weight resource at close to its full nominal
  share regardless of GHI's low absolute variance — two methods
  genuinely optimizing different effective objectives for the same
  nominal weights, hence the low rank correlation. For wind, the resource
  criterion's higher CV keeps it influential in TOPSIS too, closing most
  of the gap between the two methods.

**Classification: genuine algorithmic property of vector vs. min-max
normalization interacting with resource-criterion spatial uniformity —
not a bug, and not fixed.** No change was made to `topsis.py` or
`vikor.py` (per this task's own constraint: do not alter either formula
without confirming the cause, and this is not a defect to fix). Per
`METHODOLOGY.md` §2.3, the dual-method robustness claim is now qualified
as wind-only; solar's site ranking is derived from TOPSIS alone, with
VIKOR retained as an independent, wind-specific cross-check and reported
as a documented divergence for solar rather than a validated agreement.

`sensitivity/sensitivity_analysis.py`'s weight-perturbation-vs-VIKOR
concordance table (§2.7) is now implemented:
`run_mcda_sensitivity(region, tech, config, delta_pct=0.20) -> pd.DataFrame`
orchestrates `topsis.perturb_topsis_weights()` and
`vikor.compute_concordance()` (imported and called as-is, neither
re-implemented) — for each of the 4 criteria, weight is perturbed
+/-`delta_pct` in isolation, `run_topsis()` is re-run with the perturbed
weights, and the result is compared via `compute_concordance()` against
both the unperturbed TOPSIS baseline and the independent VIKOR baseline.
One baseline row (perturbation_pct=0.0, the TOPSIS-vs-VIKOR concordance
with no perturbation) plus 8 perturbation rows (4 criteria x 2 directions)
are written to
`outputs/tables/mcda_sensitivity_concordance_{region}_{tech}.csv` per
region/tech pair. `delta_pct` defaults to 0.20 (METHODOLOGY.md §2.7's
stated "plus or minus 20 percent") as a function parameter, not a
`ScenarioConfig` field — it is a sensitivity-design choice, not a
physical/economic parameter, the same status `perturb_topsis_weights()`'s
own `delta_pct` argument already had. Verified with synthetic criterion
rasters (mocking `get_analysis_grid`/`create_exclusion_mask`/all 5
`data_layers` loaders in both `topsis.py`'s and `vikor.py`'s own
namespaces, the same pattern used to verify those two modules themselves),
not yet against real criterion rasters — see `SPRINT_LOG.md`.

Five weighted criteria, per `config/scenario_params.yaml`'s `topsis.weights`:

| Criterion | Weight |
|---|---|
| `resource_quality` (solar irradiance or wind power density, technology-specific) | 0.35 |
| `proximity_infrastructure` | 0.25 |
| `land_availability` | 0.20 |
| `grid_distance` | 0.20 |

Weights sum to 1.0, enforced by a Pydantic model validator on
`TopsisWeights` (`config/config_loader.py`) — the config fails to load if
they do not. Land use and protected-area status enter only implicitly,
through the binary exclusion mask above, not as a sixth weighted criterion.

### Adaptive normalization — robustness check only, NOT the primary method

Following the diagnosis above, an adaptive per-criterion normalization
(min-max for `CV < cv_threshold`, vector otherwise) was added to
`_vectorized_topsis()`/`run_topsis()` as an explicit **opt-in robustness
check**, not a replacement for the published vector-normalized ranking.
`config.topsis.normalization` (`TopsisNormalization` in
`config_loader.py`) defaults to `method: "vector"`, `cv_threshold: 0.10`
— the default pipeline call `run_topsis(region, tech, config)` is
byte-identical to before this was added (verified:
`np.allclose(scores, existing_on_disk_scores)` for brazil/solar). Adaptive
mode is only invoked via `run_topsis(region, tech, config,
norm_method="adaptive")`, and writes to a separate
`topsis_suitability_{region}_{tech}_adaptive.tif` rather than overwriting
the file `site_selection.py`/`h2_potential.py` consume.

**Verified effect — a real fix for the numeric target, but with a serious
side effect that should be weighed before ever adopting it as primary.**
With `cv_threshold=0.10`, min-max normalization triggers for solar's
resource criterion in both regions (CV 0.0598 BR / 0.0152 DE, both
`< 0.10`) and stays on vector normalization for wind (CV 0.7300 BR /
0.2426 DE, both `≥ 0.10`) and for slope/distance-to-grid/distance-to-water
in all four cases (CV 0.77–1.66, unaffected). Resource-vs-suitability
Pearson correlation under `norm_method="adaptive"`:

| region | tech | vector (primary) | adaptive (robustness check) |
|---|---|---|---|
| brazil | solar | 0.1175 | **0.999999997** |
| brazil | wind | 0.7477 | 0.7477 (unchanged — vector chosen) |
| germany | solar | -0.0737 | **0.99999999996** |
| germany | wind | 0.5759 | 0.5759 (unchanged — vector chosen) |

Solar's correlation does not just cross 0.50 — min-max normalizing a
near-constant column stretches its tiny relative variation to fill the
full `[0, 1]` range, which given the criterion's 35% nominal weight makes
it dominate the Euclidean ideal/anti-ideal distance almost completely:
directly verified for brazil/solar, the adaptive suitability score matches
min-max-normalized GHI itself to within 0.0052 absolute difference
per-cell, i.e. slope/grid-distance/water-distance (65% of nominal weight
combined) contribute almost nothing to the final ranking under adaptive
normalization for solar. This is the mirror-image failure mode of the
vector-normalization problem being investigated (resource previously
contributed ~1–15% of discriminating variance despite 35% nominal weight;
under adaptive normalization for solar it contributes effectively ~100%)
— not obviously a more defensible multi-criteria ranking, just a
different one. **Do not treat "correlation > 0.50" alone as evidence
adaptive normalization is the right choice for solar** — it was, if
anything, achieved *too* completely. Whether to adopt adaptive
normalization as the actual TOPSIS method (which would require rewriting
`METHODOLOGY.md` §2.3's stated method and re-deriving every downstream
solar result) is an open decision for the research team, not resolved by
this robustness check alone.

### Solar Resource Variance and TOPSIS Suitability: A Methodological Note

**Coefficient of variation (CV) of the resource criterion**, measured
directly against the on-disk criterion rasters (same measurement
underlying the correlation numbers and the "Adaptive normalization"
section above):

| Region | Technology | Resource criterion | CV |
|---|---|---|---|
| Brazil | Solar | GHI | ~6.0% |
| Germany | Solar | GHI | ~1.5% |
| Brazil | Wind | Wind power density | ~73.0% |
| Germany | Wind | Wind power density | ~24.3% |

**Why vector normalization was retained as the primary method.** TOPSIS's
standard normalization (`r_ij = x_ij / sqrt(Σ x_kj²)`) has been found,
across simulation and empirical comparisons of normalization procedures
for TOPSIS and related distance-based MCDM methods, to produce more
consistent rankings and lower sensitivity to weight perturbation than
min-max normalization (Chakraborty and Yeh, 2009; Jahan and Edwards,
2015), a preference confirmed specifically for TOPSIS by a dedicated
comparative line of work (Vafaei, Ribeiro and Camarinha-Matos, 2016,
2018, 2021). Structurally, because vector normalization divides each
criterion by a magnitude computed across all candidate cells, a
criterion's contribution to the normalized matrix's discriminating
variance scales with that criterion's own spatial CV, not with its
nominal weight alone (same sources) — documented, expected behavior of
the method, not a defect introduced by this codebase.

**Why min-max was not adopted as the primary method.** Min-max
normalization (`r_ij = (x_ij - min_j) / (max_j - min_j)`) rescales every
criterion to fill `[0, 1]` regardless of absolute dispersion, which is
(1) more sensitive to outliers (noted across the same normalization-
comparison literature), and (2) verified directly in this codebase (see
"Adaptive normalization" above): applying it selectively to a near-
constant criterion inflates its tiny relative variation to fill the full
range, which — given resource's 35% nominal weight — measurably made
solar's final TOPSIS score numerically indistinguishable from the
min-max-normalized resource criterion alone (max per-cell deviation
0.0052, Pearson r ≈ 1.0 against resource, both regions). This does not
restore a "balanced" ranking; it replaces one single-criterion-dominated
pathology (resource contributing ~1-15% of discriminating variance
despite 35% nominal weight) with its mirror image (~100%), annihilating
slope/grid-distance/water-distance's combined 65% nominal weight.

**The low solar resource×suitability correlation is the geographically
and methodologically correct outcome, not a bug.** Measured directly:
Pearson r(resource, suitability) = 0.12 (Brazil solar) / -0.07 (Germany
solar), against 0.75 (Brazil wind) / 0.58 (Germany wind). Given solar
irradiance's low spatial CV at the scale of both study regions, and
vector normalization's documented CV-sensitivity, a low solar
correlation is the expected consequence of applying the literature-
preferred TOPSIS normalization to a spatially uniform resource criterion
— not evidence of a data, alignment, or weighting defect. This mirrors
the TOPSIS-VIKOR concordance divergence for solar already documented
above and is retained in the same spirit: a documented, diagnosed
property of the ranking, not something to force toward a target
statistic by changing methods. Practically: the suitability pattern this
study reports for solar PV siting is primarily driven by infrastructure-
accessibility criteria (slope, grid distance, water distance) rather
than GHI itself — consistent with the broader pattern, across GIS-MCDA
solar/hybrid siting studies, of infrastructure and land criteria
carrying the discriminating burden once resource quality is spatially
near-constant (cf. Doorga et al., 2019; Aydin, Kentel and Duzgun, 2013;
Sanchez-Lozano, Garcia-Cascales and Lamata, 2016) — though these sources
address solar/hybrid siting generally and do not themselves quantify
GHI's CV; that specific measurement is this study's own.

**Field convention vs. methodological literature.** Min-max normalization
is the more common default in applied GIS-MCDA renewable-siting studies
(Villacreses et al., 2017; Sanchez-Lozano, Garcia-Cascales and Lamata,
2016; Latinopoulos and Kechagia, 2015 — a wind-siting application, not
solar; Doorga et al., 2019; Aydin, Kentel and Duzgun, 2013; Al-Shammari
et al., 2021), while vector normalization is more often preferred in the
literature evaluating normalization procedures for TOPSIS specifically
(Chakraborty and Yeh, 2009; Jahan and Edwards, 2015; Vafaei, Ribeiro and
Camarinha-Matos, 2016, 2018, 2021). No single recommendation spans both
bodies of work; reviews of decision-support methods applied more broadly
to renewable energy investment have found that different methods tend
to converge on similar top-ranked alternatives even where intermediate
scores diverge (Strantzali and Aravossis, 2016). This study follows the
normalization-procedure-specific literature and keeps vector
normalization as primary.

**Robustness-check artifacts.**
`topsis_suitability_{region}_{tech}_adaptive.tif` files exist on disk for
all four region/tech combinations as an explicit sensitivity/robustness
check (see "Adaptive normalization" above for how they were generated).
They are available for inspection but do not replace
`topsis_suitability_{region}_{tech}.tif` (vector-normalized, primary) in
any downstream stage — `site_selection.py`, `h2_potential.py`, and every
candidate-sites output already generated are derived exclusively from
the vector-normalized files.

**References for this section** (verified against real, findable
publications before citing — several years/attributions in an earlier
draft prompt for this section did not check out against the literature
and were corrected or dropped rather than used as given; in particular,
no findable "Iftimie et al. 2023" paper could be located and it is not
cited here, and Hay & McKay (1985) — a real paper, but on irradiance
transposition to tilted surfaces, not on regional GHI spatial variance —
is not cited here either):

- Aydin, N.Y., Kentel, E., & Duzgun, H.S. (2013). GIS-based site selection methodology for hybrid renewable energy systems: A case study from western Turkey. *Energy Conversion and Management*, 70, 90-106.
- Al-Shammari, S., Ko, W., Al-Ammar, E.A., Alotaibi, M.A., & Choi, H.-J. (2021). Optimal decision-making in photovoltaic system selection in Saudi Arabia. *Energies*, 14(2), 357.
- Chakraborty, S., & Yeh, C.-H. (2009). A simulation comparison of normalization procedures for TOPSIS. *Proceedings of the International Conference on Computers and Industrial Engineering*.
- Doorga, J.R.S., et al. (2019). Multi-criteria GIS-based modelling technique for identifying potential solar farm sites: A case study in Mauritius. *Renewable Energy*, 133, 1201-1219.
- Jahan, A., & Edwards, K.L. (2015). A state-of-the-art survey on the influence of normalization techniques in ranking: Improving the materials selection process in engineering design. *Materials & Design*, 65, 335-342.
- Latinopoulos, D., & Kechagia, K. (2015). A GIS-based multi-criteria evaluation for wind farm site selection. A regional scale application in Greece. *Renewable Energy*, 78, 550-560.
- Sanchez-Lozano, J.M., Garcia-Cascales, M.S., & Lamata, M.T. (2016). Comparative TOPSIS-ELECTRE TRI methods for optimal sites for photovoltaic solar farms: case study in Spain. *Journal of Cleaner Production*, 127, 387-398.
- Strantzali, E., & Aravossis, K. (2016). Decision making in renewable energy investments: A review. *Renewable and Sustainable Energy Reviews*, 55, 885-898.
- Vafaei, N., Ribeiro, R.A., & Camarinha-Matos, L.M. (2016). Normalization Techniques for Multi-Criteria Decision Making: Analytical Hierarchy Process Case Study. *DoCEIS 2016*.
- Vafaei, N., Ribeiro, R.A., & Camarinha-Matos, L.M. (2018). Data normalisation techniques in decision making: case study with TOPSIS method. *International Journal of Information and Decision Sciences*.
- Vafaei, N., Ribeiro, R.A., & Camarinha-Matos, L.M. (2021). Comparison of Normalization Techniques on Data Sets with Outliers.
- Villacreses, G., Martinez-Gomez, J., Jijon, D., & Cordovez, M. (2017). Wind farms suitability location using geographical information system (GIS), based on multi-criteria decision making (MCDM) methods: The case of continental Ecuador. *Renewable Energy*, 109, 275-286.

### Literature basis for TOPSIS criterion weights

TOPSIS criterion weights were drawn from the GIS-MCDA renewable-siting
literature rather than elicited from an internal expert panel, consistent
with the study's goal of minimising subjective weighting bias. The
assigned weights (`topsis.weights` above) are: resource quality (solar
irradiance or wind power density) 35%; proximity to infrastructure 25%;
land availability 20%; distance to grid 20%. These values fall within the
ranges documented across the reviewed literature (resource quality:
12–55%; grid proximity: 15–30%), with resource quality receiving the
highest weight reflecting its primary role in determining hydrogen
production cost, while the remaining weight is distributed across
infrastructure and land access criteria. The robustness of the ranking to
these weights is checked by the ±20% weight-perturbation sensitivity
described in the "Zonal site aggregation" cross-reference above
(`run_mcda_sensitivity()`, METHODOLOGY.md §2.7) and by the VIKOR
cross-check described above in this file.

`METHODOLOGY.md` §2.3 states the same four weight values (35/25/20/20%)
and the same literature-range framing (12–55% resource quality, 15–30%
grid proximity) with no additional citation beyond the general GIS-MCDA
literature reference already present there — no author-year source was
supplied in the parameter justification draft (change_list.md esboço I)
for this specific numeric range, so none is invented here per this
documentation pass's no-fabricated-citation constraint.

## Zonal site aggregation

Sites are not selected ex ante. Implemented in `spatial/site_selection.py`:
`select_candidate_sites(region, tech, config, top_n=None,
top_suitability_percentile=0.85, verbose=False) -> gpd.GeoDataFrame`,
reading `topsis_suitability_{region}_{tech}.tif` and
`exclusion_mask_{region}.tif` directly from `data/processed/` (both already
written by prior stages — neither `topsis.py` nor `exclusion_mask.py` is
re-run or modified).

**Current values (`thresholds.min_contiguous_suitable_area_km2` = 50 km²,
`thresholds.max_contiguous_suitable_area_km2` = 2,000 km², both read from
config, never hardcoded — this section previously stated a stale 10 km²
minimum, since bumped; see the inline comment in `scenario_params.yaml`).
`top_suitability_percentile` (default 0.85) is a **function default, not a
`scenario_params.yaml` field** — flagged here since Hard Rule 4 (root
`CLAUDE.md`) otherwise requires numeric params to live only in the YAML.**

Processing order: (1) `suitable = (mask == 1) & (suitability > 0)`; (2)
restricted further to cells at or above the 85th percentile of that valid
suitability distribution (`top_suitability_percentile=0.85` — keeps only
the top 15% of cells), the fix for amorphous, bioma-scale "clusters"
(e.g. Brazil solar's `cluster_2` at 2,170,942 km² before this fix) that a
fairly uniform TOPSIS raster over a large region previously produced; (3)
contiguous groups of the surviving cells are identified via
`scipy.ndimage.label` with 8-connectivity (a 3×3 all-ones structuring
element); (4) clusters whose total area falls below
`min_contiguous_suitable_area_km2` (50 km²) are dropped; (5) any surviving
cluster whose area still exceeds `max_contiguous_suitable_area_km2`
(2,000 km²) — e.g. a uniform coastal strip that stays contiguous even
after the percentile filter — is capped down to its own top
`max_contiguous_suitable_area_km2`-worth of highest-suitability cells
(`np.partition` threshold) rather than dropped outright, which can split
one oversized cluster into several disconnected polygons via the
`unary_union` step below. Surviving clusters (or capped fragments) are
vectorized into polygons and ranked by
`mean_suitability * log1p(suitable_area_km2)` — favouring high suitability
without letting a single-pixel outlier outrank a large, moderately
suitable cluster. `run_pipeline.py`'s `stage_spatial()` calls this with
`top_n=SITE_SELECTION_TOP_N` (= 20, a `run_pipeline.py`-level constant, not
a YAML field either) to shortlist the top 20 ranked clusters per
region/tech before writing
`data/processed/candidate_sites_{region}_{tech}.geojson` and `.csv`
(geometry serialized as WKT in the CSV; the returned `GeoDataFrame` always
keeps a real geometry column, never dropped). `METHODOLOGY.md` §2.3 now
describes this exact algorithm (8-connectivity clustering, 85th-percentile
pre-filter, 50/2,000 km² area bounds, top-20 shortlist) — the stale
municipality/Landkreis zonal-statistics description and the 10 km²
threshold have been removed from that section (change_list.md item 1).

> **RESOLVED** (previously flagged here as a point to validate):
> `admin_boundaries.py`'s own module docstring documents `site_selection.py`
> as a consumer of `get_dissolved_polygon()` ("site_selection.py -> uses
> get_dissolved_polygon() for zonal statistics"), which by design always
> returns a **single dissolved polygon** per region (9 Brazilian states / 2
> German Bundesländer dissolved into one outline each), not the
> municipality/Landkreis-level granularity this section's original
> description implied. `site_selection.py` implements both an admin-unit
> `zonal_stats` branch (for a future, finer-grained boundary source) and a
> per-contiguous-cluster aggregation branch (vectorizing each surviving
> cluster into its own polygon via `rasterio.features.shapes`) — only the
> second actually executes today, since `get_dissolved_polygon()` always
> returns exactly one row. "Sites" in the current implementation are
> therefore contiguous suitable-cell patches, not administrative units.
> **Confirmed as the intended manuscript methodology**: `METHODOLOGY.md`
> §2.3 was rewritten (change_list.md item 1) to describe only the
> contiguous-raster-cluster algorithm that actually executes, and no longer
> references municipality/Landkreis zonal statistics. The unused admin-unit
> branch remains in the code for a possible future finer-grained boundary
> source (it would activate without further changes to `site_selection.py`
> if `admin_boundaries_fetch.py` were extended to fetch municipality/
> Landkreis-level geometries), but is intentionally not described in the
> manuscript methodology since it does not execute today.
>
> Separately: the admin-unit branch's `rasterstats` import is lazy (scoped
> inside that branch) because `rasterstats` — a documented project
> dependency (`ARCHITECTURE.md`, root `CLAUDE.md`) — is confirmed NOT
> installed in this environment (`pip show rasterstats` finds nothing),
> the same `requirements.txt`-is-empty gap already flagged in
> `07_risks_and_limitations.md`.

## Technical hydrogen potential (METHODOLOGY.md §2.4)

Implemented in `potential/h2_potential.py`:
`calculate_h2_potential(region, tech, config, electrolyzer_type="pem") ->
gpd.GeoDataFrame`, reading `candidate_sites_{region}_{tech}.geojson` directly
from `data/processed/` (already written by `site_selection.py` — neither
its logic nor its output is modified). Per candidate site:

- `installable_capacity_mw = suitable_area_km2 * power_density_mw_per_km2`
- `annual_electricity_yield_mwh = installable_capacity_mw * full_load_hours`
- `annual_h2_production_kg = annual_electricity_yield_mwh * 1000.0 / specific_consumption_kwh_per_kg`
- `annual_h2_production_t = annual_h2_production_kg / 1000.0`

writing `data/processed/h2_potential_{region}_{tech}.{geojson,csv}`
(geometry kept as a real column in the GeoDataFrame/GeoJSON, WKT in the
CSV — same convention as `site_selection.py`). `run_all_potential(config,
electrolyzer_type="pem")` runs both regions × both techs at one electrolyzer
technology.

All three physical parameters are resolved from `ScenarioConfig`, never
hardcoded (Hard Rule 4), via `economics/decomposition.py`'s shared
resolver functions (`_renewable_capacity_density()`,
`_renewable_full_load_hours()`, `_renewable_capacity_factor()`), imported
rather than re-implemented, so `h2_potential.py`'s technical potential and
`decomposition.py`'s LCOH always agree on the same region+technology
values:

- `power_density_mw_per_km2` — `config.technologies.<solar_pv|onshore_wind>.
  <brazil|germany>.capacity_density_mw_per_km2`, a literature-validated
  `{min, baseline, max}` range resolved via `resolve_param()` at
  `config.scenario.active`. Already stored in MW/km² directly — no
  W/m²→MW/km² unit-conversion step needed anymore (that conversion used to
  cancel out numerically for the old scalar field; removed along with it).
  See `docs/memory/10_capacity_density_assumptions.md`.
- `full_load_hours` — resolved directly from
  `config.technologies.<tech>.<region>.full_load_hours` (no longer
  recomputed as `capacity_factor * 8760.0` inline; `config_loader.py`'s
  `TechRegionParams` validator cross-checks the two agree at load time).
- `specific_consumption_kwh_per_kg` — from
  `config.electrolyzer.technologies[electrolyzer_type].efficiency_kwh_per_kg`
  (52.0 kWh/kg PEM, 51.0 kWh/kg alkaline in the current YAML — read live,
  never assumed).

> **RESOLVED** (previously flagged here as a point to validate): capacity
> density and capacity factor are now both region-specific —
> `config.technologies.<tech>.<brazil|germany>` — each backed by a
> literature-reviewed `{min, baseline, max}` range rather than a single
> shared scalar. Northeast Brazil's better resource quality (METHODOLOGY.md
> §2.1) is now reflected directly: baseline capacity factor 0.24 (solar) /
> 0.36 (wind) for Brazil vs. 0.12 / 0.32 for Germany. Capacity density
> (43-60 MW/km² solar, 4.1-13.7 MW/km² wind) is technology-specific only,
> not region-specific, by deliberate study choice — both regions use the
> same NREL/standard-spacing figures. Full rationale, literature review,
> and the `resolve_param()` migration itself:
> `docs/memory/09_methodology_assumptions.md` (capacity factor) and
> `docs/memory/10_capacity_density_assumptions.md` (capacity density).
>
> ⚠️ Point to validate / newly discovered this session: GDAL's GeoJSON
> driver tags the CRS of any `.geojson` this pipeline writes as EPSG:4326
> per RFC 7946, **without actually reprojecting** the AEA-meter coordinate
> values underneath — verified live by round-tripping a GeoDataFrame
> written in the AEA CRS through `gpd.read_file()`: the reported `.crs` is
> `EPSG:4326`, but `.total_bounds` are still the original meter values
> (e.g. `[0, 0, 10000, 10000]`, not plausible longitude/latitude degrees).
> This affects `site_selection.py`'s own
> `candidate_sites_{region}_{tech}.geojson` output, not just
> `h2_potential.py`'s. A naive `.to_crs(target_crs)` on such a file treats
> the raw meters as degrees and silently produces Inf/NaN geometries
> (confirmed: triggers `pyogrio`'s `RuntimeWarning: Infinite or NaN
> coordinate encountered` on the next write). `h2_potential.py` works
> around this by overriding the crs read back from disk with
> `RegionCRS.projected_crs_for(region)` via `set_crs(allow_override=True)`
> instead of reprojecting — correct here because the coordinates were never
> actually transformed on write, only mislabeled. Any future module reading
> one of this pipeline's own `.geojson` outputs must do the same, never
> `.to_crs()` directly against the file's self-reported CRS.

> ⚠️ Point to validate: `calculate_h2_potential()`'s output filenames
> (`data/processed/h2_potential_{region}_{tech}.{geojson,csv}`) do not
> encode `electrolyzer_type`. Re-running for the same region/tech pair with
> a different `electrolyzer_type` (e.g. PEM then alkaline) silently
> overwrites the previous file on disk, though the returned `GeoDataFrame`
> itself is always correct for the technology just requested. This mirrors
> the existing PEM-baseline/alkaline-sensitivity-only asymmetry in
> `economics/decomposition.py` (see `05_economic_model.md`), but unlike that
> module, no caller here currently persists both variants side by side —
> confirm whether a future per-technology output path is needed once
> alkaline sensitivity runs need their own on-disk record.
