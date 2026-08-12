"""Downloads land-use/land-cover rasters for both study regions: MapBiomas
Collection 9 (Brazil, direct public COG download via windowed read) and
Corine Land Cover (Germany, via a Copernicus-hosted WCS endpoint requiring
the one-time CDSE_TOKEN credential). Crops each source to the region's
bounding box and writes data/raw/landuse/<region>/landuse.tif.

Output feeds the not-yet-implemented spatial/exclusion_mask.py (binary
suitable/excluded mask) directly as a categorical raster in its native
source CRS -- unlike water_bodies_fetch.py / grid_infrastructure_fetch.py,
this module does NOT reproject onto the analysis grid, since land use is
categorical data and grid_utils.reproject_and_resample() explicitly
requires method="nearest"/"mode" (never bilinear) for such data; that
choice belongs to whichever spatial module consumes this raster, not to
acquisition (see grid_utils.py docstring).

SOURCE NOTE (Brazil): MapBiomas Collection 9 publishes national land-cover
mosaics as public Cloud-Optimized GeoTIFFs on Google Cloud Storage. Rather
than downloading the full national raster, this module opens the remote
COG directly via GDAL's /vsicurl/ virtual filesystem and reads only the
window covering Northeast Brazil's bounding box, avoiding a multi-GB
national download for a 9-state subset.

SOURCE NOTE (Germany): ARCHITECTURE.md / docs/memory/03_data_sources_and_
acquisition.md describe Corine Land Cover as reached via "Copernicus Land
Monitoring Service, one-time manual CDSE credential setup". The exact WCS
GetCoverage request shape below (endpoint, coverage identifier, subset axis
labels) follows the public EEA discomap WCS service pattern and the CDSE
token requirement already documented for this module, but has not been
exercised against a live response -- confirm against current CDSE/EEA API
documentation before the first real run. This is the same kind of
documented assumption already flagged for grid_infrastructure_fetch.py's
OSM-vs-ANEEL/MaStR deviation (see SPRINT_LOG.md).

FALLBACK NOTE (Germany): if CDSE_TOKEN is unset (credentials.py raises
MissingCredentialError) or the Corine WCS request/response fails for any
other reason, fetch_germany() falls back to ESA WorldCover 2021 -- a
public, unauthenticated 10m global land-cover product. A WMS-based
fallback (Terrascope's public viewer service) was tried first but that
host resets the connection for every GetMap/GetCapabilities request from
this environment's network path (confirmed via raw curl, independent of
this module's code); the public AWS-hosted Cloud-Optimized GeoTIFF tiles
(same open-data product, s3://esa-worldcover, verified reachable: each
3x3-degree tile the two regions' bboxes touch returns HTTP 200) are used
instead, read via /vsicurl/ windowed access and merged, the same pattern
already used for MapBiomas above. WorldCover's own class codes (10-100)
don't match Corine's legend that spatial/exclusion_mask.py's
CORINE_EXCLUDED_RANGES already reclassifies against, so the fallback
recodes WorldCover's built-up (50) and water (80) classes into
Corine-range-compatible codes before writing landuse.tif -- this keeps
exclusion_mask.py itself unchanged.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.merge import merge
from rasterio.windows import from_bounds
import requests

from src.acquisition.credentials import MissingCredentialError, get_cdse_token
from src.core.constants import Region
from src.spatial import admin_boundaries
from src.spatial.grid_utils import raster_is_populated

RAW_DIR = Path("data/raw/landuse")

# MapBiomas Collection 9 (2024 release, covering years through 2023),
# national land-cover mosaic. Not yet tracked in config/scenario_params.yaml
# despite ARCHITECTURE.md describing the collection version as "fixed in
# configuration" -- see docs/memory/03_data_sources_and_acquisition.md.
MAPBIOMAS_COLLECTION = 9
MAPBIOMAS_YEAR = 2023
MAPBIOMAS_COG_URL = (
    "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/"
    f"collection_{MAPBIOMAS_COLLECTION}/lclu/coverage/"
    f"brasil_coverage_{MAPBIOMAS_YEAR}.tif"
)

# Peak-RAM budget (bytes) for the single decoded band read out of the
# remote COG in fetch_brazil(). The national mosaic is 64149x58236 px;
# Northeast Brazil's 9-state bbox window is itself a large fraction of
# that (not a small crop), so reading it at native ~30m resolution into a
# materialized numpy array is what previously caused a multi-GB OOM crash
# -- windowing alone doesn't bound memory when the window itself is huge.
# Fixed by decimating the read via out_shape (GDAL streams/resamples the
# source block-by-block rather than materializing the full window),
# choosing an output pixel count that fits this budget regardless of how
# large the requested bbox is. MapBiomas class codes are single-byte
# (uint8), so pixel count and byte count are the same.
MAPBIOMAS_MAX_RAM_BYTES = 80 * 1024 * 1024  # ~80 MiB

# EEA discomap WCS service for Corine Land Cover 2018 (latest full CLC
# release at time of writing). See SOURCE NOTE (Germany) above.
CORINE_WCS_BASE = (
    "https://image.discomap.eea.europa.eu/arcgis/services/"
    "Corine/CLC2018_WM/MapServer/WCSServer"
)
CORINE_COVERAGE_ID = "1"

# ESA WorldCover 2021 (10m), public unauthenticated fallback for Germany --
# AWS-hosted COGs, tiled 3x3 degrees, named by SW-corner (e.g. N51E006).
# See FALLBACK NOTE above.
WORLDCOVER_S3_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_TILE_SIZE_DEG = 3

# Fallback output resolution (degrees, ~100m at these latitudes): reading
# at the source's native 10m for a whole-region bbox is both far finer than
# exclusion_mask.py's 1km analysis grid needs and, since merge() picks the
# highest-resolution input by default, would force a multi-gigapixel read
# through /vsicurl/ per tile. Passing this explicitly makes GDAL serve a
# decimated read from the COG's built-in overviews instead.
WORLDCOVER_FALLBACK_RESOLUTION_DEG = 0.0009

# ESA WorldCover class codes relevant to exclusion (10m legend).
WORLDCOVER_BUILTUP_CLASS = 50
WORLDCOVER_WATER_CLASS = 80

# Recodes WorldCover classes into values inside exclusion_mask.py's existing
# CORINE_EXCLUDED_RANGES ([(111,142), (411,423), (511,523)]), so the Germany
# reclassification branch there treats this fallback raster identically to
# a real Corine download with no consumer-side change. Any WorldCover class
# not listed here is mapped to _WORLDCOVER_SUITABLE_CODE, chosen outside all
# three excluded ranges.
_WORLDCOVER_TO_CORINE_CODE = {
    WORLDCOVER_BUILTUP_CLASS: 111,  # -> artificial surfaces (111-142)
    WORLDCOVER_WATER_CLASS: 512,    # -> water bodies (511-523)
}
_WORLDCOVER_SUITABLE_CODE = 211  # arable land -- outside all excluded ranges

HTTP_TIMEOUT_S = 120


class LandUseFetchError(RuntimeError):
    """Raised when a land-use raster cannot be downloaded or cropped."""


def _decimated_out_shape(window_width: float, window_height: float, max_pixels: int) -> tuple[int, int]:
    """
    Return (out_height, out_width) for a decimated COG read that (a) never
    exceeds `max_pixels` total pixels -- bounding the peak RAM of the
    array `rasterio` returns -- and (b) never upsamples past the window's
    own native pixel dimensions, while preserving the window's aspect
    ratio as closely as integer rounding allows.
    """
    window_width = max(1.0, window_width)
    window_height = max(1.0, window_height)
    if window_width * window_height <= max_pixels:
        return int(round(window_height)), int(round(window_width))

    aspect = window_width / window_height
    out_height = max(1, int((max_pixels / aspect) ** 0.5))
    out_width = max(1, int(max_pixels / out_height))
    return out_height, out_width


def fetch_brazil(config) -> Path:
    """Read the MapBiomas Collection 9 national coverage COG, windowed to
    Northeast Brazil's bounding box, and write
    data/raw/landuse/nordeste_br/landuse.tif. Returns the Path.

    `config` is accepted for interface parity with other acquisition
    modules but is not currently used -- the crop window is defined by the
    region's own boundary geometry, not by the analysis grid.
    """
    region = Region.NORDESTE_BR
    out_path = RAW_DIR / region.value / "landuse.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[landuse] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    print(
        f"[landuse] {region.value}: MapBiomas Collection {MAPBIOMAS_COLLECTION} "
        f"({MAPBIOMAS_YEAR}), windowed COG read"
    )

    bbox_wgs84 = admin_boundaries.get_region_bounds_wgs84(region)
    remote_path = f"/vsicurl/{MAPBIOMAS_COG_URL}"

    try:
        with rasterio.open(remote_path) as src:
            window = (
                from_bounds(*bbox_wgs84, transform=src.transform)
                .round_offsets()
                .round_lengths()
            )
            bytes_per_px = np.dtype(src.dtypes[0]).itemsize
            max_pixels = max(1, MAPBIOMAS_MAX_RAM_BYTES // bytes_per_px)
            out_height, out_width = _decimated_out_shape(window.width, window.height, max_pixels)
            print(
                f"  reading {int(window.width)}x{int(window.height)} px window "
                f"from remote COG, decimated to {out_width}x{out_height} px "
                f"(~{bytes_per_px * out_height * out_width / 1e6:.1f} MB)...",
                end="", flush=True,
            )
            t_read = time.time()
            data = src.read(
                1, window=window, out_shape=(out_height, out_width),
                resampling=Resampling.mode,
            )
            window_transform = src.window_transform(window)
            transform = window_transform * window_transform.scale(
                window.width / out_width, window.height / out_height
            )
            profile = src.profile.copy()
            print(f" done in {time.time() - t_read:.1f}s")
    except RasterioIOError as exc:
        raise LandUseFetchError(
            f"Failed to open remote MapBiomas COG at {MAPBIOMAS_COG_URL}: {exc}. "
            f"Check network connectivity and that GDAL was built with curl "
            f"support (required for /vsicurl/ remote reads)."
        ) from exc

    if data.size == 0:
        raise LandUseFetchError(
            f"Windowed read for region {region.value!r} returned an empty "
            f"array -- check bbox={bbox_wgs84} against the MapBiomas "
            f"raster's actual extent before proceeding (an empty crop would "
            f"silently produce a meaningless exclusion mask downstream)."
        )
    if not raster_is_populated(data, nodata_value=0):
        raise LandUseFetchError(
            f"Windowed read for region {region.value!r} returned data that "
            f"is entirely class 0 (nodata/unclassified) -- check "
            f"bbox={bbox_wgs84} against the MapBiomas raster's actual data "
            f"footprint, and that the /vsicurl/ read wasn't interrupted or "
            f"silently truncated. Writing this would silently produce a "
            f"100%-excluded exclusion mask downstream."
        )

    profile.update(
        {"height": data.shape[0], "width": data.shape[1], "count": 1, "transform": transform}
    )
    out_path = _write_raster(region, data, profile)

    print(
        f"[landuse] {region.value}: wrote {out_path} "
        f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
    )
    return out_path


def _worldcover_tile_ids(bbox_wgs84: tuple[float, float, float, float]) -> list[str]:
    """Return the ESA WorldCover 3x3-degree tile IDs (e.g. "N51E006")
    covering `bbox_wgs84`, named by each tile's SW corner per the product's
    published naming convention."""
    minx, miny, maxx, maxy = bbox_wgs84
    lon0 = int(math.floor(minx / WORLDCOVER_TILE_SIZE_DEG)) * WORLDCOVER_TILE_SIZE_DEG
    lon1 = int(math.floor(maxx / WORLDCOVER_TILE_SIZE_DEG)) * WORLDCOVER_TILE_SIZE_DEG
    lat0 = int(math.floor(miny / WORLDCOVER_TILE_SIZE_DEG)) * WORLDCOVER_TILE_SIZE_DEG
    lat1 = int(math.floor(maxy / WORLDCOVER_TILE_SIZE_DEG)) * WORLDCOVER_TILE_SIZE_DEG

    tile_ids = []
    lat = lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            tile_ids.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
            lon += WORLDCOVER_TILE_SIZE_DEG
        lat += WORLDCOVER_TILE_SIZE_DEG
    return tile_ids


def _fetch_worldcover_fallback(region: Region, bbox_wgs84: tuple[float, float, float, float]) -> Path:
    """Read ESA WorldCover 2021 COG tile(s) covering `bbox_wgs84` from the
    public AWS bucket via /vsicurl/ windowed access, merge and crop to the
    exact bbox, recode into Corine-range-compatible codes (see FALLBACK
    NOTE), and write data/raw/landuse/<region>/landuse.tif."""
    tile_ids = _worldcover_tile_ids(bbox_wgs84)
    urls = [f"/vsicurl/{WORLDCOVER_S3_BASE}/ESA_WorldCover_10m_2021_v200_{t}_Map.tif" for t in tile_ids]

    print(f"  ESA WorldCover 2021: {len(tile_ids)} tile(s) {tile_ids}, windowed COG read...",
          end="", flush=True)
    t_req = time.time()
    datasets = []
    try:
        datasets = [rasterio.open(u) for u in urls]
        mosaic, transform = merge(
            datasets, bounds=bbox_wgs84,
            res=(WORLDCOVER_FALLBACK_RESOLUTION_DEG, WORLDCOVER_FALLBACK_RESOLUTION_DEG),
        )
    except RasterioIOError as exc:
        raise LandUseFetchError(
            f"ESA WorldCover COG fallback failed to read tile(s) {tile_ids} "
            f"from {WORLDCOVER_S3_BASE}: {exc}. Check network connectivity "
            f"and that GDAL was built with curl support."
        ) from exc
    finally:
        for ds in datasets:
            ds.close()
    print(f" done in {time.time() - t_req:.1f}s")

    data = mosaic[0]
    if data.size == 0:
        raise LandUseFetchError(
            f"ESA WorldCover fallback crop for region {region.value!r} "
            f"returned an empty array -- check bbox={bbox_wgs84} against "
            f"tile(s) {tile_ids}."
        )

    recoded = np.full(data.shape, _WORLDCOVER_SUITABLE_CODE, dtype=np.uint16)
    for wc_code, corine_code in _WORLDCOVER_TO_CORINE_CODE.items():
        recoded[data == wc_code] = corine_code

    profile = {
        "driver": "GTiff", "height": recoded.shape[0], "width": recoded.shape[1],
        "count": 1, "dtype": "uint16", "crs": "EPSG:4326", "transform": transform,
    }
    return _write_raster(region, recoded, profile)


def fetch_germany(config) -> Path:
    """Request Corine Land Cover, cropped server-side to North Germany's
    bounding box via a WCS GetCoverage call, and write
    data/raw/landuse/germany/landuse.tif. Returns the Path.

    Falls back to ESA WorldCover 2021 (see module FALLBACK NOTE) if
    CDSE_TOKEN is unset, the WCS request fails, or the response is not a
    valid raster -- Corine access requires a one-time manual credential
    this environment may not have, and the fallback keeps the pipeline
    runnable without it.
    """
    region = Region.NORTH_GERMANY
    out_path = RAW_DIR / region.value / "landuse.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[landuse] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    bbox_wgs84 = admin_boundaries.get_region_bounds_wgs84(region)

    try:
        token = get_cdse_token()
    except MissingCredentialError as exc:
        print(
            f"[landuse] {region.value}: CDSE_TOKEN not set ({exc}); "
            f"falling back to ESA WorldCover 2021"
        )
        out_path = _fetch_worldcover_fallback(region, bbox_wgs84)
        print(
            f"[landuse] {region.value}: wrote {out_path} "
            f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
        )
        return out_path

    print(f"[landuse] {region.value}: Corine Land Cover via CDSE-authenticated WCS")

    minx, miny, maxx, maxy = bbox_wgs84
    params = {
        "SERVICE": "WCS",
        "VERSION": "2.0.1",
        "REQUEST": "GetCoverage",
        "COVERAGEID": CORINE_COVERAGE_ID,
        "FORMAT": "image/tiff",
        "SUBSET": [f"Long({minx},{maxx})", f"Lat({miny},{maxy})"],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "h2-lcoh-pipeline/1.0 (research use)",
    }

    print(
        f"  requesting WCS coverage for bbox=({minx:.2f},{miny:.2f},"
        f"{maxx:.2f},{maxy:.2f})...", end="", flush=True,
    )
    t_req = time.time()
    try:
        resp = requests.get(
            CORINE_WCS_BASE, params=params, headers=headers,
            timeout=HTTP_TIMEOUT_S, stream=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f" failed ({exc}); falling back to ESA WorldCover 2021")
        out_path = _fetch_worldcover_fallback(region, bbox_wgs84)
        print(
            f"[landuse] {region.value}: wrote {out_path} "
            f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
        )
        return out_path
    print(f" done in {time.time() - t_req:.1f}s")

    out_dir = RAW_DIR / region.value
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "landuse.tif"

    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)

    try:
        with rasterio.open(out_path) as check:
            if check.count < 1 or check.width == 0 or check.height == 0:
                raise LandUseFetchError(
                    f"Downloaded Corine Land Cover file at {out_path} has no "
                    f"readable raster data (width={check.width}, "
                    f"height={check.height})."
                )
    except (RasterioIOError, LandUseFetchError) as exc:
        print(f"  Corine response invalid ({exc}); falling back to ESA WorldCover 2021")
        out_path = _fetch_worldcover_fallback(region, bbox_wgs84)
        print(
            f"[landuse] {region.value}: wrote {out_path} "
            f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
        )
        return out_path

    print(
        f"[landuse] {region.value}: wrote {out_path} "
        f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
    )
    return out_path


def _write_raster(region: Region, data, profile: dict) -> Path:
    out_dir = RAW_DIR / region.value
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "landuse.tif"
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data, 1)
    return out_path


def fetch(region: Region, config) -> Path:
    """Unified entry point matching the acquisition-layer contract
    (`fetch(region, config) -> Path`, Hard Rule 5, root CLAUDE.md).
    Dispatches to fetch_brazil() or fetch_germany() based on region.
    """
    if region == Region.NORDESTE_BR:
        return fetch_brazil(config)
    if region == Region.NORTH_GERMANY:
        return fetch_germany(config)
    raise ValueError(f"Unrecognized region: {region!r}")
