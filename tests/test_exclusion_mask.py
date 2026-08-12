"""Offline unit tests for src/spatial/exclusion_mask.py.

Every test uses synthetic, hand-written rasters/GeoJSON under pytest's
tmp_path plus monkeypatch (never real data/raw/ or data/processed/
content), mirroring the pattern SPRINT_LOG.md documents this module having
already been verified against ad hoc (but never captured as pytest) --
this file makes that verification real, reusable, and CI-runnable.

SCOPE NOTE (ADR-010/ADR-011, 06_technical_decisions_log.md): exclusion_mask.py
implements exactly three layers -- land-use reclassification, protected-
area rasterization (now BUFFERED by a REGION-SPECIFIC distance,
config.thresholds.min_distance_to_protected_area_km.<brazil|germany>,
converted to metres via _config_helpers.get_protected_area_buffer_km()),
and the dissolved administrative-boundary clip (ADR-007) -- combined via
logical AND. It does NOT apply a slope cutoff or a grid-distance cutoff;
those are TOPSIS's own continuous weighted criteria (slope,
distance-to-grid), never binary cuts here, and the corresponding
`max_slope_degrees`/`max_distance_to_grid_km` YAML fields were removed as
confirmed-unused dead configuration (ADR-010). Tests below verify the
module's real, current (buffered protected-area, no-slope-cutoff)
behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import geopandas as gpd
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_bounds
from shapely.geometry import box

from src.core.constants import Region, RegionCRS
from src.spatial import exclusion_mask as em
from src.spatial._config_helpers import get_protected_area_buffer_km


def _stub_config(buffer_km: float = 2.0):
    """Minimal config double exposing only what create_exclusion_mask()
    reads directly (config.thresholds.min_distance_to_protected_area_km,
    now per-region -- ADR-011) -- real ScenarioConfig construction is
    exercised elsewhere (test_decomposition.py et al.); these end-to-end
    tests only need the one field, same buffer_km for both regions unless
    a test cares about the brazil/germany split specifically."""
    return SimpleNamespace(
        thresholds=SimpleNamespace(
            min_distance_to_protected_area_km=SimpleNamespace(brazil=buffer_km, germany=buffer_km)
        )
    )


def _write_raster(path, array, crs, transform):
    with rasterio.open(
        path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
        count=1, dtype=array.dtype, crs=crs, transform=transform,
    ) as dst:
        dst.write(array, 1)


def _write_vector(path, geometries, crs):
    gpd.GeoDataFrame({"geometry": geometries}, crs=crs).to_file(path, driver="GeoJSON")


def _reprojected_box(minx, miny, maxx, maxy, projected_crs):
    """Build a WGS84-degrees box whose forward reprojection to
    `projected_crs` lands back on the given projected-meters box (exact at
    this raster's 1 km pixel resolution -- the AEA curvature over a
    10x10km extent this close to the projection's own origin is
    sub-metre). Used so protected-area fixtures are written in *real*
    WGS84 degrees, matching genuine acquisition output (INDE/Natura2000
    API responses), rather than round-tripping an already-projected
    GeoDataFrame through `.to_file(driver="GeoJSON")` -- GDAL's GeoJSON
    writer tags such output as EPSG:4326 per RFC 7946 WITHOUT actually
    reprojecting the coordinate values (see SPRINT_LOG.md's h2_potential.py
    session note / docs/memory/04_spatial_methodology.md), which would
    make `_rasterize_protected_areas()`'s own `.to_crs()` call
    double-misinterpret the numbers -- not a bug in the module under test,
    but a fixture-construction pitfall this helper avoids."""
    to_wgs84 = Transformer.from_crs(projected_crs, "EPSG:4326", always_xy=True)
    lon1, lat1 = to_wgs84.transform(minx, miny)
    lon2, lat2 = to_wgs84.transform(maxx, maxy)
    return box(min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


class TestReclassifyLanduse:
    """_reclassify_landuse(): the module's core land-use binary-mask logic,
    per-region excluded class set (Brazil/MapBiomas) or ranges
    (Germany/Corine), plus the always-excluded nodata code 0."""

    def test_brazil_mapbiomas_excluded_classes(self):
        # 3 = suitable (arbitrary non-excluded class), 24 = urban, 33 =
        # water, 0 = nodata.
        array = np.array([[3, 24], [33, 0]], dtype=np.uint8)
        result = em._reclassify_landuse(array, Region.NORDESTE_BR)
        assert result.tolist() == [[1, 0], [0, 0]]

    def test_brazil_unrecognized_class_code_defaults_to_suitable(self):
        """Only NODATA_CLASS_CODE (0) and the explicit
        MAPBIOMAS_EXCLUDED_CLASSES set ({11, 24, 30, 33}) are excluded --
        any other code is treated as suitable by design (see the
        function's own docstring: the exclusion list is authoritative, the
        "suitable" label is not exhaustive)."""
        array = np.array([[255, 11], [30, 5]], dtype=np.uint16)
        result = em._reclassify_landuse(array, Region.NORDESTE_BR)
        assert result.tolist() == [[1, 0], [0, 1]]

    def test_germany_corine_excluded_ranges(self):
        # 211 = arable land (suitable), 112 = artificial surfaces
        # (excluded), 512 = water bodies (excluded), 0 = nodata.
        array = np.array([[211, 112], [512, 0]], dtype=np.uint16)
        result = em._reclassify_landuse(array, Region.NORTH_GERMANY)
        assert result.tolist() == [[1, 0], [0, 0]]

    def test_unrecognized_region_raises(self):
        array = np.zeros((2, 2), dtype=np.uint8)
        with pytest.raises(ValueError):
            em._reclassify_landuse(array, "narnia")


class TestProtectedAreaBufferResolution:
    """get_protected_area_buffer_km() (src/spatial/_config_helpers.py):
    region-specific buffer lookup, ADR-011 -- Brazil and Germany must be
    able to resolve to different values from the same config object."""

    def test_resolves_different_buffer_per_region(self):
        config = SimpleNamespace(
            thresholds=SimpleNamespace(
                min_distance_to_protected_area_km=SimpleNamespace(brazil=2.0, germany=0.5)
            )
        )
        assert get_protected_area_buffer_km(Region.NORDESTE_BR, config) == 2.0
        assert get_protected_area_buffer_km(Region.NORTH_GERMANY, config) == 0.5


class TestRasterizeBoundary:
    """_rasterize_boundary() -- ADR-007's fix: get_analysis_grid()'s
    rectangular bounding-box grid can be materially larger than the
    region's real dissolved outline, so this function clips the combined
    mask to the actual administrative-boundary polygon."""

    def test_boundary_mask_marks_only_inside_polygon(self, monkeypatch):
        region = Region.NORDESTE_BR
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)  # 10x10 grid, 1000m cells
        out_shape = (10, 10)
        # Polygon covering only the left half (columns 0-4) of the grid.
        boundary_gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 5000, 10000)]}, crs=RegionCRS.projected_crs_for(region)
        )
        monkeypatch.setattr(em, "get_dissolved_polygon", lambda r: boundary_gdf)

        mask = em._rasterize_boundary(region, out_shape, transform)

        assert mask.shape == out_shape
        assert mask[:, :5].all()       # left half: inside boundary
        assert not mask[:, 5:].any()   # right half: outside boundary

    def test_missing_boundary_geometry_raises(self, monkeypatch):
        empty_gdf = gpd.GeoDataFrame({"geometry": []}, crs=RegionCRS.projected_crs_for(Region.NORDESTE_BR))
        monkeypatch.setattr(em, "get_dissolved_polygon", lambda r: empty_gdf)
        with pytest.raises(em.ExclusionMaskError):
            em._rasterize_boundary(Region.NORDESTE_BR, (10, 10), from_bounds(0, 0, 10000, 10000, 10, 10))


class TestRasterizeProtectedAreas:
    """_rasterize_protected_areas() -- rasterizes protected-area polygons
    directly onto the reference grid, inverted to 1=unprotected/
    0=protected. buffer_m=0.0 (default) preserves the original unbuffered
    behavior exactly; buffer_m>0.0 extends the excluded footprint beyond
    each polygon's own boundary (ADR-010, module SCOPE NOTE above)."""

    def test_protected_polygon_excludes_only_its_own_footprint_zero_buffer(self, tmp_path):
        region = Region.NORDESTE_BR
        crs = RegionCRS.projected_crs_for(region)
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)
        path = tmp_path / "protected_areas.geojson"
        # Protected polygon covering only the left-most 2 columns, written
        # in real WGS84 degrees (see _reprojected_box()'s docstring).
        geom = _reprojected_box(0, 0, 2000, 10000, crs)
        _write_vector(path, [geom], "EPSG:4326")

        mask = em._rasterize_protected_areas(path, crs, (10, 10), transform, buffer_m=0.0)

        assert mask[:, :2].sum() == 0   # inside the protected polygon -> excluded (0)
        assert mask[:, 2:].all()        # outside it -> unprotected/suitable (1), no buffer margin

    def test_protected_polygon_buffer_extends_exclusion_beyond_footprint(self, tmp_path):
        """A 1000m buffer on a 1000m-pixel grid must exclude one additional
        column beyond the raw polygon's own 2-column footprint."""
        region = Region.NORDESTE_BR
        crs = RegionCRS.projected_crs_for(region)
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)  # 1000m cells
        path = tmp_path / "protected_areas.geojson"
        geom = _reprojected_box(0, 0, 2000, 10000, crs)
        _write_vector(path, [geom], "EPSG:4326")

        mask = em._rasterize_protected_areas(path, crs, (10, 10), transform, buffer_m=1000.0)

        assert mask[:, :3].sum() == 0   # raw footprint (2 cols) + 1000m buffer -> 3 cols excluded
        assert mask[:, 3:].all()        # beyond the buffer -> still unprotected/suitable

    def test_empty_geometry_file_returns_all_unprotected(self, tmp_path):
        region = Region.NORTH_GERMANY
        crs = RegionCRS.projected_crs_for(region)
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)
        path = tmp_path / "protected_areas.geojson"
        _write_vector(path, [], crs)

        mask = em._rasterize_protected_areas(path, crs, (10, 10), transform)

        assert mask.all()


class TestCreateExclusionMaskEndToEnd:
    """create_exclusion_mask(): land-use AND protected-areas AND boundary,
    verified against a real (small, synthetic) raster/vector pipeline run
    end to end, not by re-deriving the combination logic independently.
    get_analysis_grid/get_dissolved_polygon are monkeypatched; the raw
    land-use/protected-area file paths and the output directory are
    monkeypatched to tmp_path so nothing here touches real project state.
    """

    def test_suitable_area_and_pct_excluded_match_manual_calculation(self, tmp_path, monkeypatch):
        region = Region.NORDESTE_BR
        crs = RegionCRS.projected_crs_for(region)
        shape = (10, 10)
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)  # 1000m cells -> 1 km2/px

        monkeypatch.setattr(em, "get_analysis_grid", lambda r, c: (np.full(shape, np.nan), transform))
        boundary_gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 10000, 10000)]}, crs=crs)
        monkeypatch.setattr(em, "get_dissolved_polygon", lambda r: boundary_gdf)
        monkeypatch.setattr(em, "PROCESSED_DIR", tmp_path)

        landuse_path = tmp_path / "landuse.tif"
        landuse_array = np.zeros(shape, dtype=np.uint8)
        landuse_array[0:5, :] = 3    # top half: suitable class
        landuse_array[5:10, :] = 33  # bottom half: MapBiomas water -> excluded
        _write_raster(landuse_path, landuse_array, crs, transform)
        monkeypatch.setattr(em, "RAW_LANDUSE_TEMPLATE", str(landuse_path))

        protected_path = tmp_path / "protected_areas.geojson"
        _write_vector(protected_path, [], crs)  # empty -> all unprotected
        monkeypatch.setattr(em, "RAW_PROTECTED_AREAS_TEMPLATE", str(protected_path))

        mask, metadata = em.create_exclusion_mask(region, config=_stub_config(), verbose=False)

        assert metadata["total_km2"] == pytest.approx(100.0, rel=1e-6)
        assert metadata["suitable_km2"] == pytest.approx(50.0, rel=1e-6)
        assert metadata["pct_excluded"] == pytest.approx(50.0, rel=1e-6)
        assert int(mask.sum()) == 50

    def test_cells_outside_administrative_boundary_are_excluded_even_if_suitable(self, tmp_path, monkeypatch):
        """ADR-007: even land-use-suitable, unprotected cells must be
        excluded if they fall outside the region's real dissolved boundary
        polygon (which can be materially smaller than get_analysis_grid()'s
        rectangular bbox)."""
        region = Region.NORTH_GERMANY
        crs = RegionCRS.projected_crs_for(region)
        shape = (10, 10)
        transform = from_bounds(0, 0, 10000, 10000, 10, 10)

        monkeypatch.setattr(em, "get_analysis_grid", lambda r, c: (np.full(shape, np.nan), transform))
        # Boundary polygon covers only the left half of the grid.
        boundary_gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 5000, 10000)]}, crs=crs)
        monkeypatch.setattr(em, "get_dissolved_polygon", lambda r: boundary_gdf)
        monkeypatch.setattr(em, "PROCESSED_DIR", tmp_path)

        landuse_path = tmp_path / "landuse_de.tif"
        landuse_array = np.full(shape, 1, dtype=np.uint16)  # class 1: suitable everywhere (not in any excluded Corine range)
        _write_raster(landuse_path, landuse_array, crs, transform)
        monkeypatch.setattr(em, "RAW_LANDUSE_TEMPLATE", str(landuse_path))

        protected_path = tmp_path / "protected_de.geojson"
        _write_vector(protected_path, [], crs)
        monkeypatch.setattr(em, "RAW_PROTECTED_AREAS_TEMPLATE", str(protected_path))

        mask, metadata = em.create_exclusion_mask(region, config=_stub_config(), verbose=False)

        # Entirely suitable, entirely unprotected -- only the boundary clip
        # can be excluding anything, and it must cut the mask to exactly
        # the left half (right half is outside the dissolved polygon).
        assert mask[:, :5].all()
        assert not mask[:, 5:].any()
        assert metadata["total_km2"] == pytest.approx(50.0, rel=1e-6)  # boundary footprint only
