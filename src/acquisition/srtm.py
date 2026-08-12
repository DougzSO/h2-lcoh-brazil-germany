"""Downloads elevation raster covering a region's bounding box from AWS
Terrain Tiles (public, no auth) and writes a single mosaicked GeoTIFF to
data/raw/. Reprojection/resampling onto the analysis grid stays in
spatial/grid_utils.py, which reads the file this module writes."""

from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rasterio
import requests
from rasterio.merge import merge

from src.core.constants import Region
from src.spatial.admin_boundaries import get_dissolved_polygon

RAW_DIR = Path("data/raw/elevation")
# Persistent (not tempfile-based) per-tile cache: a whole-state bbox needs
# hundreds to thousands of individual tile requests, so an interrupted run
# must be able to resume without re-downloading tiles it already fetched
# -- a tempdir, cleaned up per-run, cannot do that. Nested by zoom (see
# _tile_cache_path()) since ZOOM is now a value that can legitimately
# change between sessions (see ZOOM below) and x/y tile-index ranges
# overlap across zoom levels -- a flat {x}_{y}.tif name could silently
# reuse a different zoom's tile.
TILE_CACHE_DIR = Path("data/raw/.cache/srtm_tiles")
AWS_TERRAIN_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/geotiff"
# ~152 m/px at the equator, finer at higher latitudes -- still well under
# 1/6 of the 1km analysis grid's own cell size (spatial/grid_utils.py),
# which is the actual resolution floor this raster needs to support; the
# previous zoom=12 (~38m/px) was needlessly fine for that purpose and, for
# Brazil's much larger 9-state bbox, multiplied tile count (and therefore
# wall-clock time) by ~16x for no benefit downstream.
ZOOM = 10
TIMEOUT_S = 90
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_S = 2.0  # exponential backoff between attempts: 2s, 4s
MAX_WORKERS = 4  # conservative parallel tile-download count, to avoid
                 # tripping AWS S3 rate limits on a whole-region bbox


class SRTMFetchError(RuntimeError):
    """Raised when elevation tiles cannot be downloaded or mosaicked."""


def _deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    lat_rad = math.radians(max(min(lat_deg, 85.0511), -85.0511))
    n = 2 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def _tiles_for_bbox(minx: float, miny: float, maxx: float, maxy: float,
                     zoom: int) -> list[tuple[int, int]]:
    x0, y0 = _deg2num(maxy, minx, zoom)
    x1, y1 = _deg2num(miny, maxx, zoom)
    return [(x, y) for x in range(min(x0, x1), max(x0, x1) + 1)
            for y in range(min(y0, y1), max(y0, y1) + 1)]


def _tile_cache_path(zoom: int, x: int, y: int) -> Path:
    return TILE_CACHE_DIR / str(zoom) / str(x) / f"{y}.tif"


def _download_tile(x: int, y: int, zoom: int) -> Path:
    """Download one AWS Terrain Tile, skipping the request entirely if
    it's already cached on disk from a prior (possibly interrupted) run.
    Retries up to MAX_RETRIES times with exponential backoff on any
    request failure -- transient network errors/S3 throttling are common
    across the many per-tile requests a whole-state bbox needs, more so
    now that MAX_WORKERS requests run concurrently.

    Runs in a ThreadPoolExecutor worker (see fetch()). _tiles_for_bbox()
    never produces a duplicate (x, y) pair within one fetch() call, so no
    two workers ever target the same tile_path in the same run -- but the
    write itself still goes to a unique per-attempt temp file and is
    atomically renamed into place, so a concurrent reader (or a future
    process racing this one) can never observe a partially-written tile.
    """
    tile_path = _tile_cache_path(zoom, x, y)
    if tile_path.exists() and tile_path.stat().st_size > 0:
        return tile_path

    url = f"{AWS_TERRAIN_BASE}/{zoom}/{x}/{y}.tif"
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=TIMEOUT_S)
            resp.raise_for_status()
            tile_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = tile_path.with_name(
                f"{tile_path.name}.{os.getpid()}-{threading.get_ident()}.tmp"
            )
            tmp_path.write_bytes(resp.content)
            _atomic_replace(tmp_path, tile_path)
            return tile_path
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE_S * (2 ** (attempt - 1)))
    raise SRTMFetchError(
        f"Failed to download elevation tile {zoom}/{x}/{y} after {MAX_RETRIES} "
        f"attempts: {last_exc}"
    ) from last_exc


_RENAME_RETRIES = 5
_RENAME_RETRY_DELAY_S = 0.2


def _atomic_replace(tmp_path: Path, dest_path: Path) -> None:
    """`tmp_path.replace(dest_path)`, retrying briefly on PermissionError.

    On Windows, renaming onto a just-created file can transiently fail
    with PermissionError/WinError 5 ("Acesso negado") if something else
    (most commonly antivirus real-time scanning) has the freshly-written
    destination or temp file locked for a few milliseconds -- observed
    live under MAX_WORKERS concurrent tile writes. This is filesystem
    contention, not a download failure, so it's retried locally rather
    than re-triggering _download_tile's own network retry/backoff.
    """
    last_exc: OSError | None = None
    for attempt in range(1, _RENAME_RETRIES + 1):
        try:
            tmp_path.replace(dest_path)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < _RENAME_RETRIES:
                time.sleep(_RENAME_RETRY_DELAY_S * attempt)
    raise last_exc


def fetch(region: Region, config) -> Path:
    """Download AWS Terrain Tiles covering `region`'s bounding box, mosaic
    them, and write data/raw/elevation/<region>/srtm_30m.tif. Returns the
    Path."""
    out_path = RAW_DIR / region.value / "srtm_30m.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[srtm] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    print(f"[srtm] {region.value}: AWS Terrain Tiles (elevation), zoom {ZOOM}, "
          f"{MAX_WORKERS} parallel workers")

    minx, miny, maxx, maxy = get_dissolved_polygon(region).to_crs("EPSG:4326").total_bounds
    tiles = _tiles_for_bbox(minx, miny, maxx, maxy, ZOOM)
    if not tiles:
        raise SRTMFetchError(f"No tiles computed for region {region.value!r} bounds.")
    total = len(tiles)

    TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    was_cached = [
        (p := _tile_cache_path(ZOOM, x, y)).exists() and p.stat().st_size > 0
        for x, y in tiles
    ]
    tile_paths: list[Path | None] = [None] * total
    completed = 0
    n_cached = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {
            executor.submit(_download_tile, x, y, ZOOM): i
            for i, (x, y) in enumerate(tiles)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            tile_paths[i] = future.result()
            completed += 1
            n_cached += was_cached[i]
            print(f"\r  tiles: {completed}/{total} ({completed / total * 100:.0f}%, "
                  f"{n_cached} from cache)", end="", flush=True)
    print()

    datasets = [rasterio.open(p) for p in tile_paths]
    profile = datasets[0].profile
    mosaic, transform = merge(datasets)
    for ds in datasets:
        ds.close()

    profile.update(height=mosaic.shape[1], width=mosaic.shape[2],
                    transform=transform, count=mosaic.shape[0])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic)

    size_mb = out_path.stat().st_size / 1e6
    print(f"[srtm] {region.value}: wrote {out_path} ({size_mb:.1f} MB) in {time.time() - t0:.1f}s")
    return out_path
