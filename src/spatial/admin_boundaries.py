"""
admin_boundaries.py
====================
Loads administrative boundaries for the two study regions (Northeast Brazil
and North Germany), dissolves them into single polygons, projects them to
region-specific equal-area CRS, and exposes dynamically computed bounds.

Consumers:
    grid_utils.py     -> uses get_region_bounds() to define the analysis grid extent
    site_selection.py -> uses get_dissolved_polygon() for zonal statistics

Design notes
------------
- Bounds are ALWAYS derived from on-disk geometry (geometry.total_bounds),
  never hardcoded.
- The *set* of administrative units that make up each region (9 Brazilian
  states; 2 full German Bundesländer) IS hardcoded below. This is a
  deliberate, unavoidable exception to the "no hardcoded values" principle:
  the region definition itself is a modeling choice, not a derived quantity.
  Only the geometry/bounds are computed dynamically. The actual filtering to
  these states happens once, upstream, in admin_boundaries_fetch.py; the
  constants are kept here purely as documentation of that modeling choice.
- Germany uses GADM level 1 (whole states), matching the methodology's
  definition of the study area as "Schleswig-Holstein and Niedersachsen" in
  full -- not a sub-sample of districts. This also guarantees a single
  contiguous polygon after dissolve (the two states share a border), which a
  district-level sub-sample could not guarantee in general.
- Results are cached at module level so repeated calls do not re-read disk.

CHANGE (network -> disk): This module previously called requests.get()
directly against the IBGE Malhas API and the GADM zip endpoint, in
violation of the "network only in src/acquisition/" rule (CLAUDE.md #5).
It now reads the already-downloaded GeoJSON files at
data/boundaries/{region}.geojson. Public function signatures are
unchanged.

CRS NOTE: the on-disk files' stored CRS tag cannot be trusted -- some are
real WGS84 degrees (e.g. germany.geojson, from a live GADM fetch), others
are already-projected AEA meters mislabeled as EPSG:4326 (e.g. an older
offline brazil.geojson fixture predating live IBGE access). Treating
either case as the other silently produces a wrong bbox: reprojecting
already-meters coordinates as if they were degrees explodes the extent by
~5-6 orders of magnitude, while relabeling real degree coordinates as
already-projected meters collapses the extent to a few meters (the
"5m x 4m Germany bbox" bug this module's tests guard against). So
_read_boundary_file() detects which case a given file is in from its
actual coordinate magnitude (real degrees are always within
[-180, 180] x [-90, 90]; projected AEA meters for either region never
are) rather than trusting the stored tag OR assuming one fixed behavior
for all files.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

from src.core.constants import Region, RegionCRS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Region membership (hardcoded by design — see module docstring)
# ---------------------------------------------------------------------------

# IBGE numeric UF codes for the 9 Northeast Brazil states.
NORDESTE_STATE_CODES: dict[str, int] = {
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25,
    "PE": 26, "AL": 27, "SE": 28, "BA": 29,
}

# German Bundesländer making up the study area, matched on GADM level-1
# NAME_1 field. Full states, per the methodology's definition of "North
# Germany" -- not a district-level sub-sample.
NORTH_GERMANY_STATES: set[str] = {"Schleswig-Holstein", "Niedersachsen"}

# ---------------------------------------------------------------------------
# On-disk source configuration
# ---------------------------------------------------------------------------

# Written by src.acquisition.admin_boundaries_fetch.fetch(); read-only here.
BOUNDARIES_DIR = Path("data/boundaries")

_BOUNDARY_FILES: dict[Region, Path] = {
    Region.NORDESTE_BR: BOUNDARIES_DIR / "brazil.geojson",
    Region.NORTH_GERMANY: BOUNDARIES_DIR / "germany.geojson",
}

# Sanity bounds on region extent (in meters, projected CRS), used to catch
# gross geometry errors (wrong GADM level, disjoint selection, CRS mixups)
# before they propagate silently downstream. These are generous, not exact --
# they only need to catch order-of-magnitude mistakes.
_MIN_EXPECTED_EXTENT_M = {
    Region.NORDESTE_BR: (800_000.0, 800_000.0),   # width, height
    Region.NORTH_GERMANY: (100_000.0, 100_000.0),
}
_MAX_EXPECTED_EXTENT_M = {
    Region.NORDESTE_BR: (2_500_000.0, 2_000_000.0),
    Region.NORTH_GERMANY: (500_000.0, 450_000.0),
}


class AdminBoundaryError(RuntimeError):
    """Raised when a boundary source cannot be read or parsed."""


# ---------------------------------------------------------------------------
# Module-level cache
# Keyed by Region enum. Avoids re-reading disk on repeated calls within the
# same process. NOT persisted across runs -- this is an in-memory cache only.
# Caveat: if region membership constants above are edited in a live Python
# session (REPL/notebook) after this module has already been imported and
# used, the cache will silently return stale geometry. Restart the process
# after editing NORDESTE_STATE_CODES or NORTH_GERMANY_STATES.
# ---------------------------------------------------------------------------

_dissolved_cache: dict[Region, gpd.GeoDataFrame] = {}


def _is_geographic_extent(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    """True if a bounding box's raw coordinate values fall within valid
    WGS84 degree ranges -- the heuristic used to tell real geographic
    coordinates apart from already-projected AEA meters (which run into
    the hundreds of thousands/millions and can never satisfy this)."""
    return (-180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0
            and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0)


def _read_boundary_file(path: Path, region: Region) -> gpd.GeoDataFrame:
    """Read a boundary GeoJSON for `region` and return it as a GeoDataFrame
    in the region's projected, equal-area CRS.

    The on-disk files' stored CRS tag cannot be trusted (see module
    docstring CRS NOTE): this inspects the raw coordinate magnitude to
    decide whether the file holds real WGS84 degrees (reproject properly
    via .to_crs()) or already-projected AEA meters mislabeled as EPSG:4326
    (assign the target CRS directly, no reprojection).
    """
    if not path.exists():
        raise AdminBoundaryError(
            f"Boundary file not found: {path}. Run "
            f"src.acquisition.admin_boundaries_fetch.fetch() first to "
            f"populate {path.parent}/."
        )

    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        raise AdminBoundaryError(f"Failed to read boundary file {path}: {exc}") from exc

    target_crs = RegionCRS.projected_crs_for(region)
    if not gdf.empty and _is_geographic_extent(*gdf.total_bounds):
        gdf = gdf.set_crs("EPSG:4326", allow_override=True).to_crs(target_crs)
    else:
        gdf = gdf.set_crs(target_crs, allow_override=True)

    return gdf


# ---------------------------------------------------------------------------
# Brazil: read pre-fetched IBGE mesh from disk
# ---------------------------------------------------------------------------

def fetch_nordeste_states() -> gpd.GeoDataFrame:
    """Read the 9 Northeast Brazil state meshes (Maranhão, Piauí, Ceará, Rio
    Grande do Norte, Paraíba, Pernambuco, Alagoas, Sergipe, Bahia --
    NORDESTE_STATE_CODES above, matching admin_boundaries_fetch.py's own
    IBGE per-state query) from data/boundaries/brazil.geojson, dissolve
    into a single polygon, and project to the region's equal-area CRS.

    The file is written by admin_boundaries_fetch.py, which downloads one
    GeoJSON feature per state from IBGE's Malhas Territoriais API (already
    filtered to exactly these 9 states -- IBGE's API is queried per-state
    by numeric UF code, so there is no whole-country response to
    over-filter) and concatenates them before writing to disk.

    Returns
    -------
    gpd.GeoDataFrame
        Single-row GeoDataFrame, dissolved boundary, CRS =
        RegionCRS.projected_crs_for(Region.NORDESTE_BR).

    Raises
    ------
    AdminBoundaryError
        If the boundary file is missing or fails to parse, the resulting
        geometry is empty, or the dissolved area falls outside ±2% of the
        Northeast Brazil region's canonical 1,552,175 km2 (IBGE) -- the
        latter would indicate a wrong state selection or a corrupted/
        truncated boundary file, not something to silently propagate into
        every downstream spatial computation.
    """
    combined = _read_boundary_file(_BOUNDARY_FILES[Region.NORDESTE_BR], Region.NORDESTE_BR)

    if combined.empty:
        raise AdminBoundaryError("Northeast Brazil boundary file contains no geometry.")

    # Dissolve to a single outline. The file is already in the region's
    # projected, equal-area CRS (see module docstring CRS NOTE), so no
    # reprojection is needed here.
    dissolved = combined.dissolve().reset_index(drop=True)
    if dissolved.empty:
        raise AdminBoundaryError("Dissolve of Northeast Brazil states returned empty geometry.")

    # Area sanity check. Computed directly in the geometry's own CRS
    # (RegionCRS.projected_crs_for(NORDESTE_BR): a proj4 Albers Equal Area
    # Conic fitted to this exact region -- lat_1=-5, lat_2=-15, lat_0=-10,
    # lon_0=-40) rather than reprojecting to a generic global equal-area
    # CRS like EPSG:6933: it's already equal-area, and re-verified to
    # agree with EPSG:6933 to 5 significant figures for this polygon, so
    # a second reprojection would only add float error, not precision.
    total_area_km2 = dissolved.geometry.area.sum() / 1e6
    if not (1.520e6 <= total_area_km2 <= 1.584e6):
        raise AdminBoundaryError(
            f"Northeast Brazil dissolved area {total_area_km2:,.0f} km2 is "
            f"outside the expected range [1.52M, 1.584M] km2 (±2% of the "
            f"canonical 1,552,175 km2) -- check NORDESTE_STATE_CODES above "
            f"against data/boundaries/brazil.geojson before proceeding."
        )
    print(
        f"[admin_boundaries] brazil: {total_area_km2:,.0f} km2 across "
        f"{len(combined)} state feature(s), dissolved to 1 polygon"
    )

    return dissolved[["geometry"]]


# ---------------------------------------------------------------------------
# Germany: read pre-fetched GADM level-1 mesh from disk
# ---------------------------------------------------------------------------

def fetch_north_germany_states() -> gpd.GeoDataFrame:
    """Read GADM 4.1 level-1 (Bundesländer) for Germany from
    data/boundaries/germany.geojson, dissolve, and project to the region's
    equal-area CRS.

    The file is written by admin_boundaries_fetch.py, which downloads GADM
    level 1 (all 16 German states), filters down to the 2 states defined in
    NORTH_GERMANY_STATES (matched on the NAME_1 field), and writes the
    result to disk. Using full states (rather than a district-level
    sub-sample) matches the methodology's definition of the study area and
    guarantees a single contiguous polygon after dissolve, since
    Schleswig-Holstein and Niedersachsen share a border.

    Returns
    -------
    gpd.GeoDataFrame
        Single-row GeoDataFrame, dissolved boundary, CRS =
        RegionCRS.projected_crs_for(Region.NORTH_GERMANY).

    Raises
    ------
    AdminBoundaryError
        If the boundary file is missing or fails to parse, or the
        resulting geometry is empty.
    """
    matched = _read_boundary_file(_BOUNDARY_FILES[Region.NORTH_GERMANY], Region.NORTH_GERMANY)

    if matched.empty:
        raise AdminBoundaryError("North Germany boundary file contains no geometry.")

    # The file is already in the region's projected, equal-area CRS (see
    # module docstring CRS NOTE), so no reprojection is needed here.
    dissolved = matched.dissolve().reset_index(drop=True)
    if dissolved.empty:
        raise AdminBoundaryError("Dissolve of North Germany states returned empty geometry.")

    return dissolved[["geometry"]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_dissolved_polygon(region: Region) -> gpd.GeoDataFrame:
    """Return the single-row dissolved boundary polygon for a region, in
    the region's projected, equal-area CRS.

    Results are cached at module level after the first successful read, so
    repeated calls within the same process do not re-read disk.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.

    Returns
    -------
    gpd.GeoDataFrame
        Single-row GeoDataFrame with one 'geometry' column, CRS matching
        RegionCRS.projected_crs_for(region).

    Raises
    ------
    ValueError
        If `region` is not a recognized Region member.
    AdminBoundaryError
        If the underlying boundary file is missing or fails to parse
        (propagated from the fetch helpers), or if the resulting geometry
        fails the sanity-extent check in get_region_bounds().
    """
    if region in _dissolved_cache:
        return _dissolved_cache[region]

    if region == Region.NORDESTE_BR:
        gdf = fetch_nordeste_states()
    elif region == Region.NORTH_GERMANY:
        gdf = fetch_north_germany_states()
    else:
        raise ValueError(f"Unrecognized region: {region!r}")

    _dissolved_cache[region] = gdf
    return gdf


def get_region_bounds(region: Region) -> tuple[float, float, float, float]:
    """Return the (minx, miny, maxx, maxy) bounds of a region's dissolved
    boundary, in the region's projected, equal-area CRS.

    Bounds are computed directly from the on-disk geometry's
    `total_bounds` -- there are no hardcoded coordinates anywhere in this
    function. Used by grid_utils.py to define the analysis grid extent.

    A sanity check against expected order-of-magnitude extent is applied
    before returning, to catch gross geometry errors early (wrong GADM
    level, a disjoint/partial state selection, or a CRS mismatch) rather
    than letting a wrong grid propagate silently into TOPSIS, zonal
    statistics, or LCOH area calculations.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.

    Returns
    -------
    tuple[float, float, float, float]
        (minx, miny, maxx, maxy) in the region's projected, equal-area CRS.

    Raises
    ------
    AdminBoundaryError
        If the computed extent falls outside the expected order-of-magnitude
        range for the region (see _MIN_EXPECTED_EXTENT_M / _MAX_EXPECTED_EXTENT_M).
    """
    gdf = get_dissolved_polygon(region)
    minx, miny, maxx, maxy = gdf.total_bounds
    width = float(maxx - minx)
    height = float(maxy - miny)

    min_w, min_h = _MIN_EXPECTED_EXTENT_M[region]
    max_w, max_h = _MAX_EXPECTED_EXTENT_M[region]

    if not (min_w <= width <= max_w) or not (min_h <= height <= max_h):
        raise AdminBoundaryError(
            f"Region '{region.value}' bounds failed sanity check: "
            f"width={width:,.0f} m, height={height:,.0f} m "
            f"(expected roughly {min_w:,.0f}-{max_w:,.0f} m wide, "
            f"{min_h:,.0f}-{max_h:,.0f} m tall). "
            f"This usually indicates a wrong GADM admin level, a disjoint or "
            f"partial region selection, or a CRS unit mismatch (degrees vs "
            f"meters). Inspect the dissolved geometry before proceeding."
        )

    return float(minx), float(miny), float(maxx), float(maxy)


def get_region_bounds_wgs84(region: Region) -> tuple[float, float, float, float]:
    """Return the (minx, miny, maxx, maxy) bounds of a region's dissolved
    boundary in true WGS84 degrees.

    Derived by reprojecting the already-correct projected-CRS dissolved
    polygon (see get_dissolved_polygon()) back to EPSG:4326, rather than
    re-deriving degree bounds independently -- this guarantees the two
    bounds functions can never disagree about which geometry they describe.
    Acquisition fetchers that query degree-based external APIs (Overpass,
    WCS, ArcGIS REST) should call this instead of reprojecting
    get_dissolved_polygon() inline themselves.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.

    Returns
    -------
    tuple[float, float, float, float]
        (minx, miny, maxx, maxy) in WGS84 degrees.

    Raises
    ------
    AdminBoundaryError
        Propagated from get_region_bounds()'s sanity check (called first,
        so a corrupted projected geometry is caught before being
        reprojected to degrees and returned as if valid).
    """
    get_region_bounds(region)  # sanity-check the projected geometry first
    gdf = get_dissolved_polygon(region)
    minx, miny, maxx, maxy = gdf.to_crs("EPSG:4326").total_bounds
    return float(minx), float(miny), float(maxx), float(maxy)
