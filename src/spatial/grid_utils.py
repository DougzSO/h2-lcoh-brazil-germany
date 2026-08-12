"""
Shared raster utilities for reprojecting and resampling heterogeneous
source rasters (Global Solar Atlas, Global Wind Atlas, SRTM, transmission
distance layers) onto a common 1 km^2 analysis grid.

CRS NOTE:
    Both analysis grids use custom Albers Equal Area Conic (AEA) projections
    defined in src.core.constants.RegionCRS, one centered on Northeast Brazil
    and one on North Germany. AEA guarantees area fidelity within each
    region's extent, which UTM (conformal, not equal-area) cannot at this
    geographic scale -- see RegionCRS docstring for the full rationale.

ASSUMPTIONS THAT MUST BE VALIDATED BY THE CALLER:
    - `config` (a ScenarioConfig instance) exposes:
        config.regions.<region.value>.grid_crs -> str (PROJ4 string)
      Region bounds are obtained via admin_boundaries.get_region_bounds(region)
      or passed explicitly via the `bounds` parameter (for testing).
    - Resampling method is NOT automatically chosen per data type. Passing
      "bilinear" for categorical/binary rasters (land use classes, exclusion
      masks) will silently corrupt them. Callers are responsible for choosing
      method="nearest" or "mode" for categorical data.
    - Source rasters with nodata pixels (common near coastlines and zone
      edges) are handled explicitly via src_nodata/dst_nodata below -- do not
      bypass reproject_and_resample() with a raw rasterio.warp.reproject()
      call that omits this, or nodata will blend into real data silently
      under bilinear/cubic resampling.
"""

from pathlib import Path
from typing import Tuple, Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from rasterio.transform import Affine, from_bounds

from src.core.constants import Region, RegionCRS
from src.spatial.admin_boundaries import get_region_bounds
from src.spatial._config_helpers import get_grid_crs

# Output directory for intermediate reprojected/aligned rasters.
PROCESSED_DIR = Path("data/processed")

# Explicit, safe mapping from string method names to rasterio Resampling enum.
_RESAMPLING_METHODS = {
    "bilinear": Resampling.bilinear,
    "nearest": Resampling.nearest,
    "average": Resampling.average,
    "mode": Resampling.mode,
    "cubic": Resampling.cubic,
}


def raster_is_populated(array: np.ndarray, nodata_value: Optional[float] = None) -> bool:
    """
    Return False if `array` has no usable data at all: empty, entirely
    NaN, or (when `nodata_value` is given) entirely equal to it.

    A source or reprojected raster failing this check almost always means
    an upstream fetch was interrupted, corrupted, or returned a nodata-only
    response -- not a legitimate all-nodata result (real study-area
    rasters always have some valid coverage). Callers should raise rather
    than silently propagate a meaningless constant array downstream (e.g.
    an interrupted SRTM download producing an all-nodata elevation file
    that silently reprojects into an all-zero slope, or an empty
    windowed land-cover read that silently reclassifies to 0 suitable
    pixels).
    """
    if array.size == 0:
        return False
    if nodata_value is not None:
        valid = array != nodata_value
    elif np.issubdtype(array.dtype, np.floating):
        valid = ~np.isnan(array)
    else:
        return True
    return bool(valid.any())


def _resolve_resampling(method: str) -> Resampling:
    """Translate a method string into a rasterio Resampling enum, or fail loudly."""
    if method not in _RESAMPLING_METHODS:
        raise ValueError(
            f"Unsupported resampling method '{method}'. "
            f"Supported: {sorted(_RESAMPLING_METHODS.keys())}. "
            f"Use 'nearest' or 'mode' for categorical/binary data, "
            f"'bilinear'/'cubic'/'average' for continuous data."
        )
    return _RESAMPLING_METHODS[method]


def reproject_and_resample(
    source_raster_path: Path,
    target_crs: str,
    target_resolution_km: float = 1.0,
    method: str = "bilinear",
    nodata_value: Optional[float] = None,
    verbose: bool = False,
) -> np.ndarray:
    """
    Reproject and resample a single raster to a target CRS and resolution.

    Parameters
    ----------
    source_raster_path : Path
        Path to the source raster (any CRS/resolution supported by rasterio).
    target_crs : str
        Target CRS as an EPSG string or PROJ4 string, e.g. the AEA strings
        in RegionCRS.
    target_resolution_km : float
        Target pixel size in kilometers (default 1.0 km = 1000 m). This
        assumes target_crs is a projected CRS in meters; this function
        raises if target_crs is not projected, to avoid a silent degrees/
        meters mismatch.
    method : str
        Resampling method: "bilinear", "nearest", "average", "mode", "cubic".
        Choose based on data type (see module docstring).
    nodata_value : Optional[float]
        Explicit nodata value to use for both source and destination during
        reprojection. If None, falls back to the source raster's own
        `nodata` metadata (src.nodata) if present. If neither is available,
        reprojection proceeds WITHOUT nodata masking -- this is only safe
        for rasters known to have no nodata pixels (e.g. a fully-populated
        national grid); for rasters with coastal/edge nodata (common in
        Global Solar Atlas / SRTM near coastlines), an unset nodata means
        bilinear/cubic resampling will blend nodata into real data at edges
        without any warning. Prefer passing this explicitly when in doubt.
    verbose : bool
        If True, print the reprojection's CRS/method/shape/output-path
        details on completion. Default False -- these are per-raster
        intermediate details, printed dozens of times across a full
        pipeline run; the "no nodata resolved" WARNING below is NOT gated
        by this flag and always prints regardless.

    Returns
    -------
    np.ndarray
        The reprojected/resampled array (2D, single band; if source is
        multi-band, only band 1 is processed -- extend explicitly if you
        need multi-band support).

    Raises
    ------
    FileNotFoundError
        If source_raster_path does not exist.
    ValueError
        If target_crs is not a projected, meter-based CRS, or if method
        is unsupported.
    """
    source_raster_path = Path(source_raster_path)
    if not source_raster_path.exists():
        raise FileNotFoundError(f"Source raster not found: {source_raster_path}")

    resampling_enum = _resolve_resampling(method)
    target_resolution_m = target_resolution_km * 1000.0

    with rasterio.open(source_raster_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise ValueError(
                f"Source raster {source_raster_path} has no CRS defined. "
                f"Cannot reproject without a known source CRS."
            )

        dst_crs_obj = rasterio.crs.CRS.from_string(target_crs)
        if not dst_crs_obj.is_projected:
            raise ValueError(
                f"target_crs '{target_crs}' is not a projected CRS. "
                f"target_resolution_km assumes meters; a geographic CRS "
                f"(degrees) would silently produce a wildly wrong grid."
            )

        # Resolve nodata: explicit argument takes priority, then the
        # source raster's own metadata, then None (unmasked, logged below).
        effective_nodata = nodata_value if nodata_value is not None else src.nodata
        if effective_nodata is None:
            print(
                f"[grid_utils] WARNING: no nodata value resolved for "
                f"{source_raster_path.name} -- reprojection will NOT mask "
                f"nodata pixels. If this raster has coastal/edge nodata, "
                f"bilinear/cubic resampling will silently blend it into "
                f"real data. Pass nodata_value explicitly if unsure."
            )

        # calculate_default_transform gives us a transform/shape consistent
        # with the source extent reprojected into target_crs; we then
        # override resolution explicitly to force the requested pixel size.
        transform, width, height = calculate_default_transform(
            src_crs,
            dst_crs_obj,
            src.width,
            src.height,
            *src.bounds,
            resolution=(target_resolution_m, target_resolution_m),
        )

        dst_array = np.empty((height, width), dtype=src.dtypes[0])
        if effective_nodata is not None:
            dst_array.fill(effective_nodata)

        reproject(
            source=rasterio.band(src, 1),
            destination=dst_array,
            src_transform=src.transform,
            src_crs=src_crs,
            src_nodata=effective_nodata,
            dst_transform=transform,
            dst_crs=dst_crs_obj,
            dst_nodata=effective_nodata,
            resampling=resampling_enum,
        )

        dst_profile = src.profile.copy()
        dst_profile.update(
            {
                "crs": dst_crs_obj,
                "transform": transform,
                "width": width,
                "height": height,
                "count": 1,
                "nodata": effective_nodata,
            }
        )

    # Persist reprojected raster before returning, per pipeline contract.
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{source_raster_path.stem}_reprojected.tif"
    out_path = PROCESSED_DIR / out_name

    with rasterio.open(out_path, "w", **dst_profile) as dst:
        dst.write(dst_array, 1)

    if verbose:
        print(
            f"[grid_utils] Reprojected {source_raster_path.name} -> {target_crs} "
            f"@ {target_resolution_km}km using '{method}' "
            f"(nodata={effective_nodata}): shape={dst_array.shape}, "
            f"saved to {out_path}"
        )

    return dst_array


def get_analysis_grid(
    region: Region,
    config,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    verbose: bool = False,
) -> Tuple[np.ndarray, Affine]:
    """
    Build a reference 1 km^2 analysis grid for a given region.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.
    config : ScenarioConfig
        Must expose config.regions.<region.value>.grid_crs (str, PROJ4).
    bounds : Optional[Tuple[float, float, float, float]]
        Optional (minx, miny, maxx, maxy) in the region's projected CRS.
        If None (default), fetches bounds dynamically via get_region_bounds().
        Pass explicit bounds for unit tests or offline analysis without
        requiring network access to IBGE/GADM.

    Returns
    -------
    (grid_array, transform) : Tuple[np.ndarray, Affine]
        grid_array is a float64 array of NaN placeholders at the target
        shape (this is a reference grid, not populated data -- callers use
        the shape/transform to align other layers onto it).

    Raises
    ------
    ValueError
        If region is not recognized, CRS mismatch, or computed grid
        dimensions are non-positive.
    AttributeError
        If config does not expose grid_crs for the region.
    AdminBoundaryError
        Propagated from get_region_bounds() if bounds=None and the region's
        boundary data cannot be fetched, or fails its sanity-extent check.
    """
    if not isinstance(region, Region):
        raise ValueError(f"Unknown region '{region}'. Expected a Region enum member.")

    target_crs = get_grid_crs(region, config)

    # Validate that config.grid_crs matches RegionCRS exactly. This must be
    # an exact string match (PROJ4 strings, not EPSG codes) -- any drift
    # between the YAML and RegionCRS would silently misalign pixels between
    # the analysis grid and actual boundary geometry.
    expected_crs = RegionCRS.projected_crs_for(region)
    if target_crs != expected_crs:
        raise ValueError(
            f"CRS mismatch for region {region.value}: "
            f"config.grid_crs['{region.value}'] = '{target_crs}', "
            f"but RegionCRS.projected_crs_for({region}) = '{expected_crs}'. "
            f"These MUST match exactly (they are PROJ4 strings) or grid "
            f"bounds and boundary geometry will be in different CRS, "
            f"causing silent pixel misalignment downstream. Update "
            f"config/scenario_params.yaml to match RegionCRS exactly."
        )

    if bounds is None:
        minx, miny, maxx, maxy = get_region_bounds(region)
    else:
        minx, miny, maxx, maxy = bounds

    resolution_m = 1000.0  # 1 km^2 grid
    width = int(np.ceil((maxx - minx) / resolution_m))
    height = int(np.ceil((maxy - miny) / resolution_m))

    if width <= 0 or height <= 0:
        raise ValueError(
            f"Computed non-positive grid dimensions for '{region.value}' "
            f"(width={width}, height={height}). Check bounds ordering "
            f"(minx, miny, maxx, maxy) and units."
        )

    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    grid_array = np.full((height, width), np.nan, dtype=np.float64)

    if verbose:
        print(
            f"[grid_utils] Built reference grid for '{region.value}': "
            f"shape={grid_array.shape}, crs={target_crs}, resolution=1km"
        )

    return grid_array, transform


def align_rasters(
    raster1_path: Path,
    raster2_path: Path,
    target_crs: str,
    nodata_value: Optional[float] = None,
    verbose: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Reproject two rasters to the same CRS/resolution and verify they are
    pixel-aligned (identical shape) before returning.

    NOTE: default resampling here is bilinear, which is appropriate for
    continuous inputs. If either raster is categorical (e.g. an exclusion
    mask), do not use this function as-is -- call reproject_and_resample
    directly per-raster with method="nearest" instead.

    Parameters
    ----------
    raster1_path, raster2_path : Path
    target_crs : str
    nodata_value : Optional[float]
        Passed through to both reproject_and_resample() calls. See that
        function's docstring for the coastal/edge-nodata caveat.

    Returns
    -------
    (array1, array2) : Tuple[np.ndarray, np.ndarray]
        Both arrays guaranteed to have identical shape.

    Raises
    ------
    ValueError
        If the two rasters, after reprojection, do not share the same shape.
        This is a hard stop rather than a silent crop/pad, because a shape
        mismatch usually indicates a bounds or resolution discrepancy in the
        source data that needs investigation, not automatic correction.
    """
    array1 = reproject_and_resample(
        raster1_path, target_crs, method="bilinear", nodata_value=nodata_value, verbose=verbose
    )
    array2 = reproject_and_resample(
        raster2_path, target_crs, method="bilinear", nodata_value=nodata_value, verbose=verbose
    )

    if array1.shape != array2.shape:
        raise ValueError(
            f"align_rasters: shape mismatch after reprojection -- "
            f"{raster1_path.name}: {array1.shape} vs "
            f"{raster2_path.name}: {array2.shape}. "
            f"This usually means the source rasters have different extents; "
            f"resolve upstream (e.g. via get_analysis_grid clipping) rather "
            f"than silently cropping here."
        )

    if verbose:
        print(
            f"[grid_utils] Aligned {raster1_path.name} and {raster2_path.name} "
            f"to {target_crs}: shape={array1.shape}"
        )

    return array1, array2