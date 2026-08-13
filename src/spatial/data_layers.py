"""
src/spatial/data_layers.py

Load raw resource and geographic layers (solar irradiance, wind power
density, slope) for a given region, reproject them onto that region's
analysis grid via grid_utils, and persist aligned GeoTIFFs.

CRS NOTE:
    Source rasters arrive in whatever native CRS their provider uses
    (commonly EPSG:4326 for Global Solar Atlas / Global Wind Atlas / SRTM).
    grid_utils.reproject_and_resample() reprojects each layer independently
    into config.grid_crs[region.value], so layers share a common CRS/
    resolution but NOT necessarily identical bounds/origin unless derived
    from the same source extent -- verify in exclusion_mask.py / topsis.py
    (both reproject directly onto get_analysis_grid()'s exact transform/
    shape) before pixel-wise arithmetic across layers.

RESAMPLING METHOD CHOICES (deliberate, not arbitrary):
    - solar irradiance, wind power density: bilinear (continuous fields).
    - slope: elevation is reprojected/resampled to the 1km analysis grid
      FIRST (bilinear), and slope is differenced on that already-small
      array. Differencing at native SRTM resolution first (e.g. Germany's
      36864x29184 raster) and resampling the result was tried and found to
      require tens of GB of RAM (the elevation array plus np.gradient's two
      same-shape outputs, all float64) -- a real crash, not a theoretical
      one. Resampling first understates local terrain roughness relative to
      that approach, a known tradeoff (see load_slope), acceptable given
      slope here feeds coarse 1km siting suitability, not fine-grained
      foundation/access cost estimation.

Each loader raises FileNotFoundError explicitly if the raw source file is
missing -- there is no silent skip or default-fill behavior.

REGION TYPE NOTE: Region is the Region enum (Region.NORDESTE_BR,
    Region.NORTH_GERMANY). Raw/processed filenames still use region.value
    (e.g. "nordeste_br") to preserve the existing on-disk naming convention.

UNIT CONVERSION NOTE:
    src/acquisition/solar_wind_atlas.py writes raw GHI in native source
    units (kWh/m^2/day), not the kWh/m^2/yr callers expect -- converted
    AFTER reprojection/resampling via _load_layer's `convert_fn` hook.
    Wind power density is fetched pre-converted (W/m^2) directly from
    GWA's own "power-density" layer -- no convert_fn needed, see
    load_wind_power_density's docstring.
"""

from pathlib import Path
from typing import Callable, Optional, Tuple, Dict

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject as warp_reproject

from src.core.constants import Region
from src.spatial._config_helpers import get_grid_crs

from . import grid_utils

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

# GHI: daily-average total (kWh/m^2/day) -> annual total (kWh/m^2/yr).
# 365.25 (not 365) to account for leap years, consistent with "average
# year" totals as reported by Global Solar Atlas.
GHI_DAY_TO_YEAR = 365.25


def _validate_region(region: Region) -> None:
    if not isinstance(region, Region):
        raise ValueError(f"Unknown region '{region}'. Expected a Region enum member.")


def _save_aligned(array: np.ndarray, transform, crs: str, region: Region, layer_name: str) -> Path:
    """Persist an aligned array as GeoTIFF to data/processed/, per pipeline contract."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"{region.value}_{layer_name}_aligned.tif"

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype,
        "crs": crs,
        "transform": transform,
    }

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(array, 1)

    return out_path


def _load_layer(
    region: Region,
    config,
    raw_filename_template: str,
    layer_name: str,
    units: str,
    source_name: str,
    resampling_method: str = "bilinear",
    convert_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
    verbose: bool = False,
) -> Tuple[np.ndarray, Dict]:
    """
    Shared implementation for the layer loaders below; factored out to
    avoid near-identical copy-pasted bodies drifting out of sync. Each
    public function is a thin wrapper. `convert_fn`, if given, runs AFTER
    reprojection to convert native source units into `units` (see
    load_wind_power_density for the caveat this ordering implies).
    """
    _validate_region(region)

    raw_path = RAW_DIR / raw_filename_template.format(region=region.value)
    if not raw_path.exists():
        raise FileNotFoundError(
            f"Raw {layer_name} file not found for region '{region.value}': {raw_path}. "
            f"Expected input from {source_name}."
        )

    target_crs = get_grid_crs(region, config)

    array = grid_utils.reproject_and_resample(
        source_raster_path=raw_path,
        target_crs=target_crs,
        target_resolution_km=1.0,
        method=resampling_method,
    )

    if convert_fn is not None:
        array = convert_fn(array)

    # Recover the transform actually used by reproject_and_resample by
    # re-reading the reprojected file it just wrote. This avoids duplicating
    # the reprojection transform-calculation logic here and guarantees the
    # metadata we report matches exactly what's on disk.
    reprojected_path = PROCESSED_DIR / f"{raw_path.stem}_reprojected.tif"
    with rasterio.open(reprojected_path) as src:
        transform = src.transform
        actual_crs = str(src.crs)

    out_path = _save_aligned(array, transform, actual_crs, region, layer_name)

    metadata = {
        "units": units,
        "crs": actual_crs,
        "resolution_km": 1.0,
        "source": source_name,
        "aligned_path": str(out_path),
    }

    if verbose:
        print(
            f"Loaded {layer_name} for {region.value}: {array.shape}, CRS {actual_crs}"
        )

    return array, metadata


def load_solar_irradiance(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """
    Load and align solar irradiance (Global Solar Atlas GHI) for a region.

    Reads data/raw/solar/{region}/ghi.tif (src.acquisition.solar_wind_atlas).
    Converts native GHI kWh/m^2/day to kWh/m^2/yr (x GHI_DAY_TO_YEAR).
    Returns (array, metadata); metadata keys: units, crs, resolution_km,
    source, aligned_path. `verbose=True` prints the loaded shape/CRS;
    default False (see _load_layer).
    """
    return _load_layer(
        region=region,
        config=config,
        raw_filename_template="solar/{region}/ghi.tif",
        layer_name="solar_irradiance",
        units="kWh/m^2/yr",
        source_name="Global Solar Atlas (via src.acquisition.solar_wind_atlas)",
        resampling_method="bilinear",
        convert_fn=lambda ghi_kwh_m2_day: ghi_kwh_m2_day * GHI_DAY_TO_YEAR,
        verbose=verbose,
    )


def load_wind_power_density(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """
    Load and align wind power density (Global Wind Atlas) for a region.

    Reads data/raw/wind/{region}/wind_power_density_100m.tif
    (src.acquisition.solar_wind_atlas): already W/m^2 at 100m hub height,
    fetched directly from GWA's own "power-density" layer -- no manual
    conversion here. An earlier version derived this from GWA's
    "wind-speed" (mean speed, m/s) layer via P = 0.5*rho*v_mean^3, which
    underestimates true power density (Jensen's inequality: E[v^3] >
    E[v]^3 for any non-degenerate speed distribution). GWA's own
    power-density product is modeled from each pixel's actual Weibull
    distribution, not a single mean-speed point estimate, so reading it
    directly is strictly more accurate. See
    src.acquisition.solar_wind_atlas's WIND LAYER NOTE for the live-verified
    before/after numbers.
    """
    return _load_layer(
        region=region,
        config=config,
        raw_filename_template="wind/{region}/wind_power_density_100m.tif",
        layer_name="wind_power_density",
        units="W/m^2",
        source_name="Global Wind Atlas (via src.acquisition.solar_wind_atlas)",
        resampling_method="bilinear",
        verbose=verbose,
    )


def load_slope(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """
    Load and align terrain slope for a region, derived from SRTM elevation.

    Reads data/raw/elevation/{region}/srtm_30m.tif (src.acquisition.srtm)
    and warps it directly onto get_analysis_grid()'s exact 1km transform/
    shape (bilinear) BEFORE computing slope -- differencing the raw ~30m
    raster first (e.g. Germany's 36864x29184 elevation array) needs tens of
    GB of RAM as float64 (the elevation array plus np.gradient's two
    same-shape outputs) and crashes; differencing the already-1km array
    keeps every array in this function under a few MB. Understates local
    terrain roughness relative to differencing-then-resampling, acceptable
    given slope here feeds coarse 1km siting suitability (see module
    RESAMPLING METHOD CHOICES note). Warping onto get_analysis_grid()'s own
    transform (rather than grid_utils.reproject_and_resample()'s
    independently-derived one, used by every other loader in this module)
    guarantees pixel-for-pixel alignment with TOPSIS/exclusion_mask.py's
    reference grid -- the same alignment concern exclusion_mask.py's own
    ALIGNMENT NOTE already solves the same way for land use.

    Raises FileNotFoundError if the raw elevation file hasn't been acquired.
    """
    _validate_region(region)

    elevation_path = RAW_DIR / "elevation" / region.value / "srtm_30m.tif"
    if not elevation_path.exists():
        raise FileNotFoundError(
            f"Raw elevation file not found for region '{region.value}': "
            f"{elevation_path}. Expected input from src.acquisition.srtm."
        )

    target_crs = get_grid_crs(region, config)
    reference_grid, transform = grid_utils.get_analysis_grid(region, config)
    out_shape = reference_grid.shape

    # NaN-filled (not np.empty()): np.empty() leaves whatever memory the OS
    # happened to hand back, which is often already zero-filled -- so an
    # elevation source that's entirely nodata (e.g. a corrupted/interrupted
    # SRTM download) would silently leave this array at 0.0 with no
    # indication anything went wrong, producing a bogus all-zero slope
    # below instead of a loud failure. NaN makes "nothing was actually
    # written here" detectable.
    elevation_1km = np.full(out_shape, np.nan, dtype=np.float32)
    with rasterio.open(elevation_path) as src:
        warp_reproject(
            source=rasterio.band(src, 1),
            destination=elevation_1km,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=transform,
            dst_crs=target_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    if not grid_utils.raster_is_populated(elevation_1km):
        raise ValueError(
            f"Reprojected elevation for region '{region.value}' is entirely "
            f"nodata -- {elevation_path} has no usable elevation data (an "
            f"interrupted or corrupted src.acquisition.srtm.fetch() run is "
            f"the most common cause). Re-run the SRTM fetch for this region "
            f"before computing slope; a bogus all-zero slope would silently "
            f"pass through otherwise."
        )

    # Pixel spacing is ~1000m by construction (get_analysis_grid()'s 1km
    # resolution); np.gradient() defaults to unit spacing, so the result is
    # scaled by that spacing afterward rather than passed as an argument,
    # keeping this a simple elementwise op on an already-tiny array.
    gy, gx = np.gradient(elevation_1km)
    slope = np.degrees(np.arctan(np.sqrt(gx ** 2 + gy ** 2) / 1000.0)).astype(np.float32)

    out_path = _save_aligned(slope, transform, target_crs, region, "slope")
    metadata = {
        "units": "degrees",
        "crs": target_crs,
        "resolution_km": 1.0,
        "source": "SRTM 30m (via src.acquisition.srtm)",
        "aligned_path": str(out_path),
    }
    if verbose:
        print(f"Loaded slope for {region.value}: {slope.shape}, CRS {target_crs}")
    return slope, metadata


def load_distance_to_water(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """
    Load and align distance-to-water-body layer for a region.

    Reads data/raw/water_bodies/{region}/distance_to_water.tif
    (src.acquisition.water_bodies_fetch, OSM Overpass API). Already meters.
    """
    return _load_layer(
        region=region,
        config=config,
        raw_filename_template="water_bodies/{region}/distance_to_water.tif",
        layer_name="distance_to_water",
        units="meters",
        source_name="OSM Overpass API (via src.acquisition.water_bodies_fetch)",
        resampling_method="bilinear",
        verbose=verbose,
    )


def load_distance_to_grid(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """
    Load and align distance-to-transmission-grid layer for a region.

    Reads data/raw/grid/{region}/distance_to_grid.tif (src.acquisition.
    grid_infrastructure_fetch, OSM Overpass API power infrastructure).
    Already meters.
    """
    return _load_layer(
        region=region,
        config=config,
        raw_filename_template="grid/{region}/distance_to_grid.tif",
        layer_name="distance_to_grid",
        units="meters",
        source_name="OSM Power Infrastructure (via src.acquisition.grid_infrastructure_fetch)",
        resampling_method="bilinear",
        verbose=verbose,
    )