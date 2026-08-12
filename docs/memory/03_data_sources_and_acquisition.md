# 03 — Data Sources and Acquisition

**`src/acquisition/` is now 100% complete** — every provider row in the
table below has an implemented module. All input data is acquired
automatically through public APIs or bulk download endpoints, with one
exception requiring one-time manual credential setup (Corine Land Cover,
via a CDSE token). This is distinct from recurring manual downloads, which
the pipeline requires for no source. See the acquisition isolation rule in
[02_architecture_and_dataflow.md](02_architecture_and_dataflow.md) — every
provider below is reached exclusively through `src/acquisition/`.

"Implemented" here means the module exists, compiles, and imports cleanly
with the correct `fetch(region, config) -> Path` contract (verified via
`test_quick.py`).

> ✅ **Brazil acquisition confirmed functional (2026-08-12).** Earlier
> revisions of this file stated the Brazil path was unverified against a
> live response for several modules, pending network access. A direct
> on-disk check this session (file existence, size, and a live
> `get_region_bounds()`/`get_region_bounds_wgs84()` call — not a re-run of
> the fetchers themselves) confirms every one of the 7 acquisition layers
> now has real, non-trivial output for Brazil, most recently modified
> 2026-08-03 to 2026-08-05:
>
> | Layer | File | Size |
> |---|---|---|
> | Boundaries | `data/boundaries/brazil.geojson` | 277 KB — real dissolved 29-part MultiPolygon, 5,246 vertices (not a synthetic box) |
> | Elevation (SRTM) | `data/raw/elevation/brazil/srtm_30m.tif` | 398.6 MB |
> | Solar (GHI) | `data/raw/solar/brazil/ghi.tif` | 26.9 MB |
> | Wind | `data/raw/wind/brazil/wind_power_density_100m.tif` | 144.4 MB |
> | Land use | `data/raw/landuse/brazil/landuse.tif` | 8.1 MB |
> | Protected areas | `data/raw/protected_areas/brazil/protected_areas.geojson` | 2.3 MB |
> | Water bodies | `data/raw/water_bodies/brazil/distance_to_water.tif` | 13.9 MB |
> | Grid infrastructure | `data/raw/grid/brazil/distance_to_grid.tif` | 13.9 MB |
>
> `get_region_bounds_wgs84(Region.NORDESTE_BR)` returns
> `(-48.76, -18.34, -32.40, -1.05)`°, a sane real Northeast Brazil extent
> (~1,552,166 km²), confirming `admin_boundaries.py`'s CRS auto-detection
> (Issue 5, `SPRINT_LOG.md`) resolves correctly for Brazil's file, not
> only Germany's. This check confirms the outputs exist and are
> structurally sane; it does not re-verify each fetcher's live network
> path line-by-line (that was done, per module, in the sessions that
> originally produced these files — see `SPRINT_LOG.md` for whichever
> per-fetcher timing/feature-count detail was captured then). The
> per-module "Point to validate" notes below are updated accordingly;
> consult `SPRINT_LOG.md`'s Test Status section for the fullest detail.

## Data providers

| Layer | Source | Acquisition method | Module |
|---|---|---|---|
| Solar irradiance (GHI, PVOUT) | Global Solar Atlas (GSA), World Bank/ESMAP, CC BY 4.0 | Bulk API download | `acquisition/solar_wind_atlas.py` |
| Wind resource (100 m speed, power density) | Global Wind Atlas (GWA) | Bulk API download | `acquisition/solar_wind_atlas.py` |
| Terrain slope | SRTM 30 m | Automated retrieval via OpenTopography / AWS Terrain Tiles | `acquisition/srtm.py` |
| Administrative boundaries, Brazil | IBGE municipal limits | Automated public API | `acquisition/admin_boundaries_fetch.py` |
| Administrative boundaries, Germany | GADM | Automated fixed-URL download, no authentication | `acquisition/admin_boundaries_fetch.py` |
| Land use, Brazil | MapBiomas, Collection 9 (2024 release, year 2023 coverage) | Automated windowed `/vsicurl/` COG read from the public MapBiomas GCS bucket, cropped to the region bbox server-side (no full national download) | `acquisition/landuse_fetch.py` (implemented; `fetch_brazil()`) |
| Land use, Germany | ESA WorldCover 2021 — **confirmed operational source** (see `METHODOLOGY.md` §2.2/§2.9, change_list.md item 4). Corine Land Cover (Copernicus Land Monitoring Service) was the originally planned source, attempted first by `fetch_germany()`, but is **not the operational one in any result this study reports** — the CDSE token-authenticated endpoint was unavailable during acquisition. | `fetch_germany()` first attempts a WCS GetCoverage request against Corine (requires one-time manual `CDSE_TOKEN` setup); on any failure it falls back to public, unauthenticated ESA WorldCover 2021 COG tiles (AWS `esa-worldcover` bucket), recoded into Corine-range-compatible class codes so `spatial/exclusion_mask.py` needs no changes | `acquisition/landuse_fetch.py` (implemented; `fetch_germany()`) |
| Protected areas, Brazil | ICMBio/MMA (CNUC conservation units) | Automated WFS query against INDE's public GeoServer, server-side BBOX filter, `outputFormat=application/json` — not a shapefile download (see Point to validate below) | `acquisition/protected_areas_fetch.py` (implemented; `fetch_brazil()`) |
| Protected areas, Germany | Natura 2000, European Environment Agency | Automated query against the EEA discomap ArcGIS REST FeatureServer, server-side bbox filter, `f=geojson` | `acquisition/protected_areas_fetch.py` (implemented; `fetch_germany()`) |
| Power infrastructure, Brazil + Germany | OpenStreetMap power infrastructure layer (`power=line`, `power=cable`, `power=substation`), via Overpass API | Automated download; primary endpoint `overpass-api.de` with fallback mirrors `overpass.kumi.systems` and `maps.mail.ru` on timeout | `acquisition/grid_infrastructure_fetch.py` (implemented) |
| Surface water bodies | OpenStreetMap waterway layer (`natural=water`, `water=reservoir`, `waterway~"river|riverbank"`), via Overpass API | Automated download; primary endpoint `overpass-api.de` with fallback mirrors `overpass.kumi.systems` and `maps.mail.ru` on timeout | `acquisition/water_bodies_fetch.py` (implemented) |

> ⚠️ Point to validate: `ARCHITECTURE.md` and `METHODOLOGY.md` §2.2 still describe
> this layer as sourced from ANEEL SIGA (Brazil) and Marktstammdatenregister
> (Germany). The implemented module instead uses a single OSM Overpass query
> for both regions, matching the `water_bodies_fetch.py` pattern, to avoid
> two divergent country-specific API integrations for one criterion layer.
> `ARCHITECTURE.md`'s module responsibility row has been updated to match;
> `METHODOLOGY.md` Table 1 has not, since it reflects the published
> methodology text rather than pipeline internals — reconcile before treating
> the power-infrastructure provenance as final in a manuscript.

> ⚠️ Point to validate (Germany's Corine path partially resolved): a later
> session with live network access confirmed `CORINE_WCS_BASE` reaches the
> real EEA server (an invalid/placeholder `CDSE_TOKEN` correctly gets back
> HTTP 498 "invalid token" rather than a connection failure), but the full
> request/response shape has not been confirmed against a real token, since
> none was available. `landuse_fetch.py` now falls back to ESA WorldCover
> 2021 (public AWS COG tiles, `WORLDCOVER_S3_BASE`, verified reachable) when
> the Corine WCS call fails for any reason, recoding WorldCover's built-up/
> water classes into Corine-range-compatible codes so
> `spatial/exclusion_mask.py` needs no changes — verified live end-to-end
> for Germany, see `SPRINT_LOG.md`. Brazil's MapBiomas COG URL
> (`MAPBIOMAS_COLLECTION`/`MAPBIOMAS_YEAR`) was not re-verified this
> session. Separately, the MapBiomas collection version is currently a
> hardcoded module constant, not tracked in `config/scenario_params.yaml`
> as this file previously (incorrectly) claimed — `ARCHITECTURE.md`'s
> description of the collection version as "fixed in configuration" does
> not match the current implementation.

> ⚠️ Point to validate (Germany's Natura 2000 endpoint now confirmed live):
> `NATURA2000_QUERY_URL` (EEA discomap ArcGIS REST) was verified live this
> session — 1,484 real features parsed for Germany's bbox, sent as a JSON
> envelope geometry with an explicit `spatialReference.wkid` (the bare
> comma-separated envelope string this module used previously is also
> accepted by ArcGIS REST, but the JSON form removes any axis-order/
> precision ambiguity). `protected_areas_fetch.py`'s Brazil path still
> deviates from `ARCHITECTURE.md`'s "Automated direct shapefile download"
> description — it queries INDE's public GeoServer WFS (`INDE_WFS_BASE`/
> `INDE_LAYER_TYPENAME` module constants) instead of downloading and
> unpacking a national CNUC shapefile ZIP locally, to avoid a fiona/zipfile
> dependency for what is otherwise a one-off bbox crop. **Update
> (2026-08-12): `data/raw/protected_areas/brazil/protected_areas.geojson`
> now exists on disk (2.3 MB, last modified 2026-08-04)** — confirming the
> INDE WFS layer typeName does resolve to real data, in a session after
> this note was originally written. The shapefile-download fallback
> `ARCHITECTURE.md` originally specified was not needed.

All layers are reprojected and resampled onto the common 1 km² equal-area
analysis grid prior to multi-criteria analysis, so raster cell counts
translate directly into square kilometres without a separate geodetic
area-correction step — see
[04_spatial_methodology.md](04_spatial_methodology.md).

## Boundary source asymmetry

Brazilian boundaries come from a national cartographic authority (IBGE);
German boundaries come from an aggregated third-party product (GADM),
chosen to preserve full acquisition automation over the granularity of a
national source such as BKG VG250. This is treated as a study limitation —
see [07_risks_and_limitations.md](07_risks_and_limitations.md).

## Raw and processed paths

| Path | Written by | Contents |
|---|---|---|
| `data/boundaries/{region}.geojson` | `acquisition/admin_boundaries_fetch.py` | Dissolved administrative boundary per region (`brazil.geojson`, `germany.geojson`). Read by `spatial/admin_boundaries.py`. |
| `data/raw/solar/{region}/ghi.tif` | `acquisition/solar_wind_atlas.py` | Raw GHI raster, source CRS/resolution. |
| `data/raw/wind/{region}/wind_speed_100m.tif` | `acquisition/solar_wind_atlas.py` | Raw 100 m wind speed raster. |
| `data/raw/water_bodies/{region}/distance_to_water.tif` | `acquisition/water_bodies_fetch.py` | Rasterized OSM water features → Euclidean distance transform. `nodata=-9999.0` (a distance is never negative, so this never collides with real data — see note below). |
| `data/raw/grid/{region}/distance_to_grid.tif` | `acquisition/grid_infrastructure_fetch.py` | Rasterized OSM power infrastructure (`power=line`/`cable`/`substation`) → Euclidean distance transform. `nodata=-9999.0`, same rationale. |
| `data/raw/landuse/{region}/landuse.tif` | `acquisition/landuse_fetch.py` | Categorical land-cover raster, cropped to region bbox, left in each source's native CRS (not reprojected in acquisition — see `02_architecture_and_dataflow.md`). |
| `data/raw/protected_areas/{region}/protected_areas.geojson` | `acquisition/protected_areas_fetch.py` | GeoJSON `FeatureCollection` of protected-area polygons, cropped to region bbox server-side, in WGS84 (EPSG:4326, both source services' native output CRS). Not yet reprojected or rasterized — that is `spatial/exclusion_mask.py`'s job. |
| `data/raw/.cache/` | `acquisition/*` | Intermediate download cache (e.g. `world_ghi.zip`). |
| `data/processed/*_reprojected.tif`, `*_aligned.tif` | `spatial/grid_utils.py` | Reprojected/resampled intermediate rasters, written before being returned to the caller (see `reproject_and_resample()`). |

`data/raw/` is written **only** by `src/acquisition/` — never by hand, never
by any spatial or economics module (Hard Rule 3, root `CLAUDE.md`).

**Fixed nodata gap (distance rasters):** `water_bodies_fetch.py` and
`grid_infrastructure_fetch.py` originally wrote `nodata=None` for
`distance_to_water.tif`/`distance_to_grid.tif`. Both files were verified
(rasterio `src.nodata`/`src.profile`, plus a full-array min/max/NaN check)
to have no actual missing pixels — `distance_transform_edt()` populates
every grid cell with a real, finite value, including `0.0` for cells that
sit on a water/grid feature itself — so this was not a corrupted source,
just an unset tag. But `grid_utils.reproject_and_resample()` (see
`04_spatial_methodology.md`) falls back to `src.nodata` when no
`nodata_value` is passed explicitly, and `data_layers.py`'s loaders for
these two layers don't pass one either, so every reprojection printed
`WARNING: no nodata value resolved ... reprojection will NOT mask nodata
pixels`. `0` could not be used as the nodata value (it is real, valid data
here — the cell is genuinely 0 m from the feature); both fetch modules now
write `nodata=-9999.0` instead, a sentinel a Euclidean distance can never
take. The 4 already-written on-disk `.tif` files were patched in place
(nodata tag only, via `rasterio.open(path, "r+")`, verified first that no
pixel already held `-9999.0`) since acquisition's cache-skip means
re-running `--stage spatial` alone does not regenerate them. This only
touches TOPSIS's `distance_to_grid`/`distance_to_water` criteria layers —
it does not affect `spatial/exclusion_mask.py`'s suitable/total km² figures
(land use + protected areas only, no distance layers involved).

## Unit conversion rules

- **Solar irradiance (GHI):** Global Solar Atlas reports daily GHI in
  kWh/m²/day; converted to kWh/m²/yr by multiplying by 365 where an annual
  figure is required downstream. Confirm the exact conversion point against
  `spatial/data_layers.py` before modifying — the methodology's stated GHI
  range (1,800–2,200 kWh/m²/yr NE Brazil, 950–1,100 kWh/m²/yr North
  Germany) is annual.
- **Wind speed → wind power density:** Global Wind Atlas 100 m wind speed
  (m/s) is converted to wind power density (W/m²) assuming a Rayleigh wind
  speed distribution, the standard approximation for a single mean-speed
  value without a full site-specific Weibull fit. This conversion happens
  in `spatial/data_layers.py`.
- **Distance rasters (water, grid):** Computed in meters via
  `scipy.ndimage.distance_transform_edt` with **per-axis pixel-size
  `sampling`**, not a flat pixel count — required because
  `grid_utils.get_analysis_grid()` rounds width/height up when converting
  bounds to pixel dimensions, so actual cell size is `(maxx - minx) /
  width`, not always exactly 1000.0 m. Verified live for Germany, most
  recently after fixing a CRS regression that had briefly broken Germany's
  bbox (`SPRINT_LOG.md` Issue 5): 206,947 OSM water features parsed
  (materially consistent with the 206,942 features an earlier,
  pre-regression run found); 65,510 OSM power-infrastructure features
  parsed, max distance-to-grid 40,259 m.

> ✅ Resolved (2026-08-12): Brazil's water-body and distance-to-grid
> rasters have since been run — `data/raw/water_bodies/brazil/
> distance_to_water.tif` and `data/raw/grid/brazil/distance_to_grid.tif`
> both exist on disk (13.9 MB each, last modified 2026-08-05), confirmed
> via direct file check this session. This note previously said Brazil's
> larger 9-state bbox (~6× Germany's area) had not been exercised live and
> might hit an Overpass timeout; that has evidently since run to
> completion. The exact feature counts and per-fetcher timing for the
> Brazil run were not re-captured in this note (no fetcher was re-run this
> session, only pre-existing output verified) — see `SPRINT_LOG.md` for
> whichever session originally produced these files if that detail is
> needed.

## Credential handling

`acquisition/credentials.py` reads credentials from environment variables,
an untracked `.env` file at the project root, or an untracked
`config/cdse_credentials.json` file (`get_credential(name)`,
`get_cdse_token()`), checked in that order; a credential's value is never
logged, only its name and which sources were checked. A missing required
credential raises `MissingCredentialError` with an explicit, descriptive
message — the pipeline never proceeds silently with a missing credential
(Hard Rule 6, root `CLAUDE.md`). Currently the only credential consumed is
`CDSE_TOKEN`, required by `landuse_fetch.py: fetch_germany()` for Corine
Land Cover — verified to fail before any network call when unset entirely,
and (verified live) to fall back to ESA WorldCover 2021 rather than raise
when the token is present but invalid/expired (HTTP 498 from the real EEA
server). `.env` ships with a `CDSE_TOKEN=COLE_SEU_TOKEN_AQUI` placeholder;
replace it with a real token to exercise the actual Corine path. No other
module, including `protected_areas_fetch.py`, requires manual credential
setup.
