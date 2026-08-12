"""Builds the binary suitable/excluded site-suitability mask (METHODOLOGY.md
2.3): reclassifies each region's categorical land-use raster
(landuse_fetch.py's output) into suitable=1/excluded=0, rasterizes each
region's protected-area polygons (protected_areas_fetch.py's output) onto
the same grid, and combines both via logical AND. Writes
data/processed/exclusion_mask_<region>.tif.

Protected areas, water bodies, and built-up/urban land are hard exclusion
constraints here, never weighted TOPSIS criteria (METHODOLOGY.md 2.3).
Continuous criteria -- slope, resource quality -- are deliberately NOT
applied as binary cutoffs in this module; they belong to
spatial/topsis.py's weighted scoring.

PROTECTED-AREA BUFFER: protected-area polygons are buffered by a
REGION-SPECIFIC distance (config.thresholds.min_distance_to_protected_area_km,
resolved per region via _config_helpers.get_protected_area_buffer_km(),
converted to metres) before rasterizing, per METHODOLOGY.md 2.3 (ADR-010,
ADR-011, docs/memory/06_technical_decisions_log.md). Brazil keeps the
original 2 km buffer (federal protected areas are strict no-use reserves);
Germany uses 500 m, since its protected-areas dataset is Natura 2000, which
permits sustainable renewable-energy development under EU law -- a uniform
2 km buffer excluded ~60% of the German study region, inconsistent with
actual German RE siting practice.

ALIGNMENT NOTE: this module does NOT call grid_utils.reproject_and_resample()
for the land-use raster. That function computes its own output transform
from the source raster's reprojected extent, which is not guaranteed to
match get_analysis_grid()'s transform (derived from the region's
administrative boundary bounds) pixel-for-pixel -- see data_layers.py's
CRS NOTE ("NOT necessarily aligned to identical bounds/origin ... verify
downstream in exclusion_mask.py"). Instead, this module warps the land-use
raster directly onto get_analysis_grid()'s exact transform/shape via
rasterio.warp.reproject, the same reference grid protected-area geometries
are rasterized onto, guaranteeing the two masks are pixel-aligned before
the logical AND. This does not change any CRS definition (still resolved
exclusively via RegionCRS.projected_crs_for()) -- only how the raster is
snapped onto the existing reference grid.

RECLASSIFICATION NOTE: MapBiomas Collection 9 and Corine Land Cover class
codes below are taken from published legend documentation, not verified
against a live-fetched raster's actual class distribution (no network
access when this module was written -- landuse_fetch.py's live download is
itself unverified, see docs/memory/03_data_sources_and_acquisition.md).
Confirm both class-code sets against a real raster before treating the
exclusion mask as final for either region.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Tuple

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject as warp_reproject

from src.core.constants import Region, RegionCRS
from src.spatial._config_helpers import get_protected_area_buffer_km
from src.spatial.admin_boundaries import get_dissolved_polygon
from src.spatial.grid_utils import get_analysis_grid, raster_is_populated

RAW_LANDUSE_TEMPLATE = "data/raw/landuse/{region}/landuse.tif"
RAW_PROTECTED_AREAS_TEMPLATE = "data/raw/protected_areas/{region}/protected_areas.geojson"
PROCESSED_DIR = Path("data/processed")

# MapBiomas Collection 9 class codes treated as hard exclusions (Brazil).
# 11 = wetland, 24 = urban/built-up infrastructure, 30 = mining, 33 = water.
MAPBIOMAS_EXCLUDED_CLASSES = {11, 24, 30, 33}

# Corine Land Cover class-code ranges treated as hard exclusions (Germany).
# 111-142 = artificial surfaces, 411-423 = wetlands, 511-523 = water bodies.
CORINE_EXCLUDED_RANGES = [(111, 142), (411, 423), (511, 523)]

# Class code 0 is treated as nodata/unclassified for both taxonomies (not a
# valid class in either legend) and is always excluded.
NODATA_CLASS_CODE = 0


class ExclusionMaskError(RuntimeError):
    """Raised when the exclusion mask cannot be built for a region."""


def _reclassify_landuse(array: np.ndarray, region: Region) -> np.ndarray:
    """Reclassify a categorical land-use array into suitable(1)/excluded(0),
    using the per-region excluded-class set/ranges above. Everything not
    explicitly excluded (and not nodata) is treated as suitable -- the
    "Suitable: ..." class lists in METHODOLOGY.md-derived task instructions
    are illustrative, not exhaustive, so this errs toward the explicit
    exclusion list being authoritative."""
    excluded = array == NODATA_CLASS_CODE

    if region == Region.NORDESTE_BR:
        for code in MAPBIOMAS_EXCLUDED_CLASSES:
            excluded |= array == code
    elif region == Region.NORTH_GERMANY:
        for lo, hi in CORINE_EXCLUDED_RANGES:
            excluded |= (array >= lo) & (array <= hi)
    else:
        raise ValueError(f"Unrecognized region: {region!r}")

    return (~excluded).astype(np.uint8)


def _reproject_landuse_to_grid(
    raw_path: Path, target_crs: str, out_shape: tuple, transform
) -> np.ndarray:
    """Warp the raw land-use raster directly onto the reference grid's exact
    transform/shape (nearest-neighbor, since class codes are categorical).
    See module ALIGNMENT NOTE for why this bypasses
    grid_utils.reproject_and_resample()."""
    with rasterio.open(raw_path) as src:
        dst = np.zeros(out_shape, dtype=src.dtypes[0])
        warp_reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=target_crs,
            dst_nodata=NODATA_CLASS_CODE,
            resampling=Resampling.nearest,
        )
    return dst


def _rasterize_protected_areas(
    geojson_path: Path, target_crs: str, out_shape: tuple, transform, buffer_m: float = 0.0
) -> np.ndarray:
    """Rasterize protected-area polygons onto the reference grid. Returns a
    uint8 mask with 1=unprotected/suitable, 0=protected/excluded (inverted
    from the raw rasterize() output, where a burned-in feature = 1).

    buffer_m : float
        Exclusion buffer distance in metres, applied to each protected-area
        polygon AFTER reprojecting to target_crs (already in metres, per
        RegionCRS.projected_crs_for()) and BEFORE rasterizing -- so a cell
        within `buffer_m` of a protected area's boundary is excluded even
        though it falls outside the polygon itself. 0.0 (default) preserves
        the original unbuffered behavior exactly."""
    gdf = gpd.read_file(geojson_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf_projected = gdf.to_crs(target_crs)
    if buffer_m > 0:
        gdf_projected = gdf_projected.copy()
        gdf_projected["geometry"] = gdf_projected.geometry.buffer(buffer_m)

    shapes = [
        (geom, 1) for geom in gdf_projected.geometry if geom is not None and not geom.is_empty
    ]
    if not shapes:
        print(
            "  WARNING: no protected-area geometries to rasterize -- "
            "proceeding with an all-unprotected mask (check the source "
            "GeoJSON before trusting this result)"
        )
        protected_raster = np.zeros(out_shape, dtype=np.uint8)
    else:
        protected_raster = rasterize(
            shapes, out_shape=out_shape, transform=transform, fill=0, dtype="uint8"
        )

    return (1 - protected_raster).astype(np.uint8)


def _rasterize_boundary(region: Region, out_shape: tuple, transform) -> np.ndarray:
    """Rasterize the region's dissolved administrative boundary
    (admin_boundaries.get_dissolved_polygon()) onto the reference grid:
    1=inside the boundary, 0=outside.

    get_analysis_grid()'s grid_array is shaped to the region's rectangular
    BOUNDING BOX (get_region_bounds()), not its actual outline -- for an
    elongated/irregular shape like Northeast Brazil's 9-state outline, the
    bbox is materially larger than the polygon it encloses (verified live:
    3,461,794 km2 bbox vs. 1,552,166 km2 dissolved polygon, a 2.23x
    discrepancy). Without this function, every downstream raster (land-use,
    protected areas, and by extension every TOPSIS/VIKOR criterion, since
    they all key off create_exclusion_mask()'s mask) was being scored over
    the full bbox, including cells geographically outside the region
    entirely -- ocean, neighboring states/Bundesländer -- with nothing
    checking polygon containment. AND-ing this into the combined mask below
    closes that gap without touching land-use/protected-area classification
    logic or TOPSIS/VIKOR's own scoring math.

    get_dissolved_polygon(region)'s geometry is already in
    RegionCRS.projected_crs_for(region) (assigned directly on read, not
    reprojected -- see admin_boundaries.py's own CRS NOTE), the same CRS
    `transform` is defined in here, so no .to_crs() call is needed --
    site_selection.py's own zonal-stats branch relies on this same fact.
    """
    boundary_gdf = get_dissolved_polygon(region)
    shapes = [
        (geom, 1) for geom in boundary_gdf.geometry if geom is not None and not geom.is_empty
    ]
    if not shapes:
        raise ExclusionMaskError(
            f"get_dissolved_polygon({region.value!r}) returned no usable "
            f"geometry -- cannot build a boundary mask. Check "
            f"data/boundaries/{region.value}.geojson."
        )
    return rasterize(shapes, out_shape=out_shape, transform=transform, fill=0, dtype="uint8")


def create_exclusion_mask(region: Region, config, verbose: bool = False) -> Tuple[np.ndarray, Dict]:
    """Build and write the binary suitable/excluded exclusion mask for a
    region: land-use reclassification AND protected-area rasterization AND
    the dissolved administrative boundary (see _rasterize_boundary()).

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.
    config : ScenarioConfig
    verbose : bool
        If True, print intermediate per-layer reprojection/reclassification
        detail (land-use pixel counts, protected-area rasterization timing).
        Default False: only the single final summary line prints (suitable
        area, total area, percent excluded). The empty-protected-area
        WARNING below is NOT gated by this flag and always prints.

    Returns
    -------
    (mask, metadata) : Tuple[np.ndarray, Dict]
        mask is uint8, shape matching get_analysis_grid(region, config),
        1=suitable, 0=excluded. metadata includes area statistics and the
        output path.

    Raises
    ------
    FileNotFoundError
        If the raw land-use raster or protected-areas GeoJSON is missing --
        run the corresponding acquisition fetch() first.
    ExclusionMaskError
        If the reprojected land-use array and rasterized protected-area
        mask end up with mismatched shapes (should not happen given both
        are built against the same reference grid; a hard stop here is
        cheaper than a silent pixel-wise misalignment downstream).
    """
    t0 = time.time()

    target_crs = RegionCRS.projected_crs_for(region)
    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape

    landuse_path = Path(RAW_LANDUSE_TEMPLATE.format(region=region.value))
    if not landuse_path.exists():
        raise FileNotFoundError(
            f"Raw land-use raster not found for region '{region.value}': "
            f"{landuse_path}. Run src.acquisition.landuse_fetch.fetch() first."
        )
    protected_path = Path(RAW_PROTECTED_AREAS_TEMPLATE.format(region=region.value))
    if not protected_path.exists():
        raise FileNotFoundError(
            f"Protected-areas GeoJSON not found for region '{region.value}': "
            f"{protected_path}. Run src.acquisition.protected_areas_fetch.fetch() first."
        )

    t_lu = time.time()
    landuse_raw = _reproject_landuse_to_grid(landuse_path, target_crs, out_shape, transform)
    if not raster_is_populated(landuse_raw, nodata_value=NODATA_CLASS_CODE):
        raise ExclusionMaskError(
            f"Reprojected land-use raster for region '{region.value}' is "
            f"entirely nodata/class-{NODATA_CLASS_CODE} -- {landuse_path} has "
            f"no usable land-cover data (an interrupted or corrupted "
            f"acquisition.landuse_fetch.fetch() run is the most common "
            f"cause). This would otherwise silently reclassify to 0 "
            f"suitable pixels. Re-run the land-use fetch for this region "
            f"before building the exclusion mask."
        )
    landuse_mask = _reclassify_landuse(landuse_raw, region)
    if verbose:
        print(
            f"  land-use: reprojected + reclassified onto {out_shape} grid in "
            f"{time.time() - t_lu:.1f}s ({int(landuse_mask.sum())} of "
            f"{landuse_mask.size} px suitable)"
        )

    t_pa = time.time()
    buffer_m = get_protected_area_buffer_km(region, config) * 1000.0
    protected_mask = _rasterize_protected_areas(
        protected_path, target_crs, out_shape, transform, buffer_m=buffer_m
    )
    if verbose:
        print(
            f"  protected areas: rasterized onto {out_shape} grid ({buffer_m:.0f}m "
            f"buffer) in {time.time() - t_pa:.1f}s ({int(protected_mask.sum())} of "
            f"{protected_mask.size} px unprotected)"
        )

    boundary_mask = _rasterize_boundary(region, out_shape, transform)

    if not (landuse_mask.shape == protected_mask.shape == boundary_mask.shape):
        raise ExclusionMaskError(
            f"Shape mismatch building exclusion mask for {region.value!r}: "
            f"landuse_mask={landuse_mask.shape}, "
            f"protected_mask={protected_mask.shape}, "
            f"boundary_mask={boundary_mask.shape}. All three were built "
            f"against the same reference grid -- investigate before proceeding."
        )

    combined_mask = (
        (landuse_mask == 1) & (protected_mask == 1) & (boundary_mask == 1)
    ).astype(np.uint8)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"exclusion_mask_{region.value}.tif"
    profile = {
        "driver": "GTiff",
        "height": combined_mask.shape[0],
        "width": combined_mask.shape[1],
        "count": 1,
        "dtype": "uint8",
        "crs": target_crs,
        "transform": transform,
        "nodata": None,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(combined_mask, 1)

    pixel_area_km2 = abs(transform.a) * abs(transform.e) / 1e6
    # total_km2 is the boundary polygon's own rasterized footprint, NOT
    # combined_mask.size * pixel_area_km2 (the rectangular bbox grid's
    # area) -- see _rasterize_boundary()'s docstring for why that
    # distinction matters (bbox is ~2.2x the actual NE Brazil area).
    total_km2 = int(boundary_mask.sum()) * pixel_area_km2
    suitable_km2 = int(combined_mask.sum()) * pixel_area_km2
    pct_excluded = 100.0 * (1.0 - suitable_km2 / total_km2) if total_km2 > 0 else 0.0

    metadata = {
        "crs": target_crs,
        "resolution_km": 1.0,
        "shape": combined_mask.shape,
        "total_km2": round(total_km2, 1),
        "suitable_km2": round(suitable_km2, 1),
        "pct_excluded": round(pct_excluded, 1),
        "output_path": str(out_path),
    }

    print(
        f"[exclusion_mask] {region.value}: {suitable_km2:,.0f} km2 suitable / "
        f"{total_km2:,.0f} km2 total ({pct_excluded:.1f}% excluded), wrote "
        f"{out_path} in {time.time() - t0:.1f}s"
    )
    return combined_mask, metadata
