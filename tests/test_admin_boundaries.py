"""Offline unit tests for src/spatial/admin_boundaries.py.

Every test redirects `_BOUNDARY_FILES` (via monkeypatch) to a synthetic
GeoJSON fixture written under pytest's tmp_path -- never reads this
project's real data/boundaries/*.geojson, so results are independent of
whatever happens to already be on disk in a given environment.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import box

from src.core.constants import Region, RegionCRS
from src.spatial import admin_boundaries as ab


def _same_crs(actual, region: Region) -> bool:
    """Compare CRS objects via pyproj equality, not raw string equality --
    geopandas/pyproj round-tripping a PROJ4 string through a CRS object
    can append normalization noise (e.g. a trailing '+type=crs') that
    doesn't change the actual defined projection."""
    return CRS(actual) == CRS(RegionCRS.projected_crs_for(region))


@pytest.fixture(autouse=True)
def _clear_module_cache():
    """admin_boundaries.py caches dissolved geometry at module level
    (_dissolved_cache) -- clear it before and after every test so one
    test's fixture never leaks into another's assertions."""
    ab._dissolved_cache.clear()
    yield
    ab._dissolved_cache.clear()


def _write_boundary(path, region: Region, geometries) -> None:
    gdf = gpd.GeoDataFrame({"geometry": geometries}, crs=RegionCRS.projected_crs_for(region))
    gdf.to_file(path, driver="GeoJSON")


class TestReadAndDissolve:
    def test_get_dissolved_polygon_returns_single_row_correct_crs(self, tmp_path, monkeypatch):
        path = tmp_path / "brazil.geojson"
        # 1,245,800 x 1,245,800 m ~= 1,552,018 km2, inside fetch_nordeste_states()'s
        # new ±2%-of-1,552,175-km2 area sanity check.
        _write_boundary(path, Region.NORDESTE_BR, [box(0, 0, 1_245_800, 1_245_800)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, path)

        result = ab.get_dissolved_polygon(Region.NORDESTE_BR)

        assert len(result) == 1
        assert list(result.columns) == ["geometry"]
        assert _same_crs(result.crs, Region.NORDESTE_BR)

    def test_stored_crs_tag_is_ignored_in_favor_of_regioncrs(self, tmp_path, monkeypatch):
        """Per the module's own documented CRS NOTE: on-disk files may
        carry a mismatched/misleading CRS tag (e.g. from the GeoJSON
        RFC-7946 write quirk this pipeline already has to work around
        elsewhere -- see docs/memory/04_spatial_methodology.md). The
        region's projected CRS must always be assigned directly, never
        trusted from or reprojected via the file's own tag."""
        path = tmp_path / "germany.geojson"
        # Write tagged as plain WGS84 with coordinates that are only valid
        # as already-projected AEA meters (mirrors the real on-disk files).
        gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 300_000, 300_000)]}, crs="EPSG:4326")
        gdf.to_file(path, driver="GeoJSON")
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, path)

        result = ab.get_dissolved_polygon(Region.NORTH_GERMANY)

        assert _same_crs(result.crs, Region.NORTH_GERMANY)
        minx, miny, maxx, maxy = result.total_bounds
        assert (minx, miny, maxx, maxy) == pytest.approx((0.0, 0.0, 300_000.0, 300_000.0))

    def test_multiple_touching_polygons_dissolve_into_one_row(self, tmp_path, monkeypatch):
        path = tmp_path / "brazil.geojson"
        # Two touching boxes forming an L-shape spanning a larger extent.
        # Combined area = 1,000,000 + 552,000 = 1,552,000 km2, inside
        # fetch_nordeste_states()'s new area sanity check.
        geometries = [
            box(0, 0, 1_000_000, 1_000_000),
            box(1_000_000, 0, 1_600_000, 920_000),
        ]
        _write_boundary(path, Region.NORDESTE_BR, geometries)
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, path)

        result = ab.get_dissolved_polygon(Region.NORDESTE_BR)

        assert len(result) == 1
        minx, miny, maxx, maxy = result.total_bounds
        assert (minx, miny, maxx, maxy) == pytest.approx((0.0, 0.0, 1_600_000.0, 1_000_000.0))


class TestGetRegionBounds:
    def test_bounds_match_synthetic_geometry_exactly(self, tmp_path, monkeypatch):
        path = tmp_path / "germany.geojson"
        _write_boundary(path, Region.NORTH_GERMANY, [box(50_000, 60_000, 350_000, 360_000)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, path)

        bounds = ab.get_region_bounds(Region.NORTH_GERMANY)

        assert bounds == pytest.approx((50_000.0, 60_000.0, 350_000.0, 360_000.0))

    def test_too_small_extent_fails_sanity_check(self, tmp_path, monkeypatch):
        # Uses Germany, not Brazil: a 1km x 1km box would also trip
        # fetch_nordeste_states()'s new area sanity check first (a
        # different error message) rather than exercising
        # get_region_bounds()'s own extent check this test targets.
        path = tmp_path / "germany.geojson"
        _write_boundary(path, Region.NORTH_GERMANY, [box(0, 0, 1_000, 1_000)])  # 1km x 1km, way below 100km min
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, path)

        with pytest.raises(ab.AdminBoundaryError, match="sanity check"):
            ab.get_region_bounds(Region.NORTH_GERMANY)

    def test_too_large_extent_fails_sanity_check(self, tmp_path, monkeypatch):
        path = tmp_path / "germany.geojson"
        _write_boundary(path, Region.NORTH_GERMANY, [box(0, 0, 5_000_000, 5_000_000)])  # way above 500km max
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, path)

        with pytest.raises(ab.AdminBoundaryError, match="sanity check"):
            ab.get_region_bounds(Region.NORTH_GERMANY)

    def test_valid_extent_for_both_regions(self, tmp_path, monkeypatch):
        brazil_path = tmp_path / "brazil.geojson"
        # 1,245,800 x 1,245,800 m ~= 1,552,018 km2, inside the ±2% area
        # sanity check (a 1,500,000 x 1,500,000 box would be 2,250,000 km2,
        # well outside it).
        _write_boundary(brazil_path, Region.NORDESTE_BR, [box(0, 0, 1_245_800, 1_245_800)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, brazil_path)

        germany_path = tmp_path / "germany.geojson"
        _write_boundary(germany_path, Region.NORTH_GERMANY, [box(0, 0, 300_000, 300_000)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, germany_path)

        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            minx, miny, maxx, maxy = ab.get_region_bounds(region)
            assert maxx > minx and maxy > miny


class TestErrorHandling:
    def test_missing_file_raises_admin_boundary_error(self, tmp_path, monkeypatch):
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, tmp_path / "does_not_exist.geojson")
        with pytest.raises(ab.AdminBoundaryError, match="not found"):
            ab.get_dissolved_polygon(Region.NORDESTE_BR)

    def test_unparseable_file_raises_admin_boundary_error(self, tmp_path, monkeypatch):
        path = tmp_path / "germany.geojson"
        path.write_text("this is not valid geojson {{{")
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORTH_GERMANY, path)
        with pytest.raises(ab.AdminBoundaryError):
            ab.get_dissolved_polygon(Region.NORTH_GERMANY)

    def test_unrecognized_region_raises_value_error(self):
        # NOTE: Region is a str-Enum (class Region(str, Enum)), so a raw
        # string equal to a member's *value* (e.g. "brazil") would compare
        # equal to Region.NORDESTE_BR via == and be silently accepted here
        # -- this is intentional str-Enum behavior, not a bug, but means
        # this test must use a string that matches no member's value at
        # all to actually exercise the else-branch ValueError.
        with pytest.raises(ValueError):
            ab.get_dissolved_polygon("narnia")


class TestCaching:
    def test_repeated_calls_return_the_same_cached_object(self, tmp_path, monkeypatch):
        path = tmp_path / "brazil.geojson"
        _write_boundary(path, Region.NORDESTE_BR, [box(0, 0, 1_245_800, 1_245_800)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, path)

        first = ab.get_dissolved_polygon(Region.NORDESTE_BR)
        second = ab.get_dissolved_polygon(Region.NORDESTE_BR)
        assert first is second

    def test_cache_survives_source_file_deletion(self, tmp_path, monkeypatch):
        path = tmp_path / "brazil.geojson"
        _write_boundary(path, Region.NORDESTE_BR, [box(0, 0, 1_245_800, 1_245_800)])
        monkeypatch.setitem(ab._BOUNDARY_FILES, Region.NORDESTE_BR, path)

        ab.get_dissolved_polygon(Region.NORDESTE_BR)  # populate cache
        path.unlink()  # remove the source file entirely

        # Should not raise -- served from the module-level cache, no re-read.
        result = ab.get_dissolved_polygon(Region.NORDESTE_BR)
        assert len(result) == 1
