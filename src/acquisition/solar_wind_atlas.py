"""Downloads solar (Global Solar Atlas GHI) and wind (Global Wind Atlas
wind speed) rasters covering a region's bounding box and writes them to
data/raw/solar/<region>/ and data/raw/wind/<region>/. Reprojection/
resampling onto the analysis grid stays in spatial/grid_utils.py, which
reads the files this module writes.

SOURCE NOTE: Global Wind Atlas exposes a per-country GeoTIFF API
(globalwindatlas.info/api/gis/country/<ISO3>/<layer>/<height>), so wind
data is fetched per-country and cropped to the region bbox -- the same
country-then-crop shape as this module's solar path. Global Solar Atlas
does NOT expose an equivalent per-country/region endpoint (verified: its
per-country/segment download keys are dead, confirmed via S3 NoSuchKey);
the only currently-working download is a single whole-world GHI GeoTIFF
(~2.7 GB zipped). That file is cached under CACHE_DIR so it is downloaded
once and reused across regions instead of once per region.

WIND LAYER NOTE: fetches GWA's "power-density" layer (already W/m^2)
directly, not "wind-speed". An earlier version fetched mean wind speed and
approximated power density in data_layers.py as 0.5*rho*v_mean^3 -- a
known-biased estimator (Jensen's inequality: E[v^3] > E[v]^3 for any
non-degenerate wind speed distribution) since it cubes the LONG-TERM MEAN
speed rather than the true intra-year distribution. GWA's own
"power-density" product is derived from each pixel's actual modeled
Weibull distribution, not a single mean-speed point estimate, so using it
directly is strictly more accurate and removes an entire approximation
step. Verified live for both study regions (see SPRINT_LOG.md): Germany
mean 473.7 W/m^2, Brazil mean 162.1 W/m^2 -- both higher than the old
mean-speed-cubed approximation (298 / 124 W/m^2 respectively), consistent
with the correction direction Jensen's inequality predicts."""

from __future__ import annotations

import tempfile
import time
import zipfile
from pathlib import Path

import rasterio
import requests
from rasterio.errors import WindowError
from rasterio.windows import Window, from_bounds

from src.core.constants import Region
from src.spatial import admin_boundaries

SOLAR_RAW_DIR = Path("data/raw/solar")
WIND_RAW_DIR = Path("data/raw/wind")
CACHE_DIR = Path("data/raw/.cache")

GSA_GHI_WORLD_URL = (
    "https://api.globalsolaratlas.info/download/World/"
    "World_GHI_GISdata_LTAy_AvgDailyTotals_GlobalSolarAtlas-v2_GEOTIFF.zip"
)
GSA_GHI_INNER_NAME = (
    "World_GHI_GISdata_LTAy_AvgDailyTotals_GlobalSolarAtlas-v2_GEOTIFF/GHI.tif"
)

GWA_API_BASE = "https://globalwindatlas.info/api/gis/country"
WIND_LAYER = "power-density"
WIND_HEIGHT_M = 100
COUNTRY_ISO3 = {Region.NORDESTE_BR: "BRA", Region.NORTH_GERMANY: "DEU"}

TIMEOUT_S = 45
CHUNK_SIZE = 1 << 20  # 1 MiB


class SolarWindFetchError(RuntimeError):
    """Raised when solar or wind rasters cannot be downloaded or cropped."""


def _region_bounds(region: Region) -> tuple[float, float, float, float]:
    """True WGS84 degree bounds for `region`, used against the global GHI
    raster and the per-country GWA raster (both in geographic CRS)."""
    return admin_boundaries.get_region_bounds_wgs84(region)


def _download_with_progress(url: str, dest: Path, label: str) -> Path:
    """Stream `url` to `dest`, printing bytes-downloaded/total and elapsed
    time as it goes."""
    t0 = time.time()
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT_S) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            written = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
                    written += len(chunk)
                    if total:
                        pct = written / total * 100
                        print(f"\r  {label}: {written / 1e6:,.0f}/{total / 1e6:,.0f} MB "
                              f"({pct:.1f}%)", end="", flush=True)
                    else:
                        print(f"\r  {label}: {written / 1e6:,.0f} MB", end="", flush=True)
    except requests.RequestException as exc:
        raise SolarWindFetchError(f"Failed to download {label} from {url}: {exc}") from exc
    print(f"  -- done in {time.time() - t0:.1f}s")
    return dest


def _extract_with_progress(zip_path: Path, inner_name: str, dest_dir: Path) -> Path:
    """Extract a single member from `zip_path` into `dest_dir`, printing
    bytes-extracted/total as it goes."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / Path(inner_name).name
    t0 = time.time()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            total = zf.getinfo(inner_name).file_size
            written = 0
            with zf.open(inner_name) as src, open(out_path, "wb") as dst:
                while chunk := src.read(CHUNK_SIZE):
                    dst.write(chunk)
                    written += len(chunk)
                    pct = written / total * 100 if total else 0
                    print(f"\r  extracting {inner_name.split('/')[-1]}: "
                          f"{written / 1e6:,.0f}/{total / 1e6:,.0f} MB ({pct:.1f}%)",
                          end="", flush=True)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise SolarWindFetchError(f"Failed to extract {inner_name!r} from {zip_path}: {exc}") from exc
    print(f"  -- done in {time.time() - t0:.1f}s")
    return out_path


def _crop_to_bbox(src_path: Path, bounds: tuple[float, float, float, float], out_path: Path) -> Path:
    """Crop `src_path` to `bounds` (WGS84 degrees), clamping the requested
    window to the source raster's own valid pixel extent first. Without
    this, a bbox that only partially overlaps the source (e.g. a
    per-country GWA raster whose edge falls just inside the region bbox,
    or floating-point bounds landing a fraction of a pixel outside the
    world GHI raster) produces negative offsets or an out-of-bounds read
    that rasterio may silently pad with garbage/nodata rather than error."""
    minx, miny, maxx, maxy = bounds
    with rasterio.open(src_path) as src:
        requested = from_bounds(minx, miny, maxx, maxy, src.transform)
        full = Window(0, 0, src.width, src.height)
        try:
            window = requested.intersection(full).round_offsets().round_lengths()
        except WindowError as exc:
            raise SolarWindFetchError(
                f"Requested bbox {bounds} does not overlap {src_path.name} "
                f"(source raster bounds: {src.bounds}) at all: {exc}"
            ) from exc

        if window.width <= 0 or window.height <= 0:
            raise SolarWindFetchError(
                f"Requested bbox {bounds} does not overlap {src_path.name} "
                f"(source raster bounds: {src.bounds}) -- cropped window "
                f"would be empty (width={window.width}, height={window.height})."
            )

        data = src.read(window=window)
        transform = src.window_transform(window)
        profile = src.profile

    profile.update(height=data.shape[1], width=data.shape[2], transform=transform)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data)
    return out_path


def fetch_solar(region: Region, config) -> Path:
    """Download the Global Solar Atlas world GHI raster (cached across
    calls), crop it to `region`'s bounding box, and write
    data/raw/solar/<region>/ghi.tif. Returns the Path."""
    out_path = SOLAR_RAW_DIR / region.value / "ghi.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[solar] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    print(f"[solar] {region.value}: Global Solar Atlas GHI (world raster, cropped locally)")
    bounds = _region_bounds(region)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / "world_ghi.zip"
    if zip_path.exists():
        print(f"  using cached {zip_path} ({zip_path.stat().st_size / 1e6:,.0f} MB)")
    else:
        _download_with_progress(GSA_GHI_WORLD_URL, zip_path, "GHI world raster")

    # Extracted-TIF cache: persists across regions/runs so the 2.7GB zip is
    # only ever unzipped once, not once per region (extraction, not just
    # download, is the expensive step -- see module docstring).
    world_tif_path = CACHE_DIR / "world_ghi.tif"
    if world_tif_path.exists() and world_tif_path.stat().st_size > 0:
        print(f"[solar] {region.value}: using cached extracted TIF at {world_tif_path}")
    else:
        print(f"[solar] {region.value}: extracting GHI.tif from zip (one-time, ~30s)...")
        _extract_with_progress(zip_path, GSA_GHI_INNER_NAME, CACHE_DIR).replace(world_tif_path)

    print(f"[solar] {region.value}: cropping to bbox and writing {out_path}")
    _crop_to_bbox(world_tif_path, bounds, out_path)

    size_mb = out_path.stat().st_size / 1e6
    print(f"[solar] {region.value}: wrote {out_path} ({size_mb:.2f} MB) in {time.time() - t0:.1f}s")
    return out_path


def fetch_wind(region: Region, config) -> Path:
    """Download the Global Wind Atlas per-country wind power-density
    raster (already W/m^2 -- see module WIND LAYER NOTE), crop it to
    `region`'s bounding box, and write
    data/raw/wind/<region>/wind_power_density_<height>m.tif. Returns the
    Path."""
    out_path = WIND_RAW_DIR / region.value / f"wind_power_density_{WIND_HEIGHT_M}m.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[wind] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    iso3 = COUNTRY_ISO3.get(region)
    if iso3 is None:
        raise SolarWindFetchError(f"No ISO3 country mapping for region {region.value!r}.")
    print(f"[wind] {region.value}: Global Wind Atlas power-density @ {WIND_HEIGHT_M}m "
          f"(country {iso3}, cropped to region bbox)")
    bounds = _region_bounds(region)

    with tempfile.TemporaryDirectory() as tmp:
        country_tif = Path(tmp) / f"{iso3}_power-density_{WIND_HEIGHT_M}m.tif"
        url = f"{GWA_API_BASE}/{iso3}/{WIND_LAYER}/{WIND_HEIGHT_M}"
        _download_with_progress(url, country_tif, f"{iso3} power-density {WIND_HEIGHT_M}m")
        _crop_to_bbox(country_tif, bounds, out_path)

    with rasterio.open(out_path) as src:
        wpd = src.read(1, masked=True)
    size_mb = out_path.stat().st_size / 1e6
    print(
        f"[wind] {region.value}: wrote {out_path} ({size_mb:.2f} MB) in "
        f"{time.time() - t0:.1f}s -- power density min={wpd.min():.1f} "
        f"mean={wpd.mean():.1f} max={wpd.max():.1f} W/m^2"
    )
    return out_path


def fetch(region: Region, config) -> dict[str, Path]:
    """Download both solar (GHI) and wind (power density) rasters for
    `region`. Returns {"solar": Path, "wind": Path}."""
    return {"solar": fetch_solar(region, config), "wind": fetch_wind(region, config)}
