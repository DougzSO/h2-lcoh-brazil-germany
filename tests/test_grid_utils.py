"""Offline unit tests for src/spatial/grid_utils.py.

get_analysis_grid() tests use explicit `bounds=` (no network / no
admin_boundaries.get_region_bounds() call). reproject_and_resample()
tests build tiny synthetic in-memory-sized GeoTIFF fixtures under
pytest's tmp_path -- no real acquired data required.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from config.config_loader import load_scenario_config
from src.core.constants import Region, RegionCRS
from src.spatial.grid_utils import (
    _resolve_resampling,
    get_analysis_grid,
    reproject_and_resample,
)

CONFIG = load_scenario_config()


@pytest.fixture(autouse=True)
def _cleanup_processed_dir():
    """reproject_and_resample() always persists to the real data/processed/
    (per pipeline contract, Hard Rule 7) rather than any tmp_path passed by
    a test -- clean up whatever a test creates there so this offline suite
    never leaves stray *_reprojected.tif fixtures behind in real project
    state."""
    processed_dir = Path("data/processed")
    before = set(processed_dir.glob("*")) if processed_dir.exists() else set()
    yield
    after = set(processed_dir.glob("*")) if processed_dir.exists() else set()
    for path in after - before:
        if path.is_file():
            path.unlink()


def _fake_config(grid_crs: str):
    """Minimal stand-in exposing only what get_grid_crs()/get_analysis_grid()
    read: config.regions.<region.value>.grid_crs."""
    region_cfg = SimpleNamespace(grid_crs=grid_crs)
    return SimpleNamespace(regions=SimpleNamespace(brazil=region_cfg, germany=region_cfg))


def _write_raster(path, bounds, crs, shape=(5, 5), fill_value=1.0, dtype="float64"):
    transform = from_bounds(*bounds, shape[1], shape[0])
    array = np.full(shape, fill_value, dtype=dtype)
    with rasterio.open(
        path, "w", driver="GTiff", height=shape[0], width=shape[1], count=1,
        dtype=dtype, crs=crs, transform=transform,
    ) as dst:
        dst.write(array, 1)
    return path


class TestGetAnalysisGridDimensions:
    def test_shape_matches_ceil_of_extent_over_1km(self):
        bounds = (500_000.0, 9_500_000.0, 800_000.0, 9_700_000.0)  # 300km x 200km
        grid, transform = get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=bounds)
        assert grid.shape == (200, 300)  # (height, width)

    def test_non_divisible_extent_rounds_up(self):
        # 300,500 m wide -> ceil(300.5) = 301 columns; 200,100 m tall -> 201 rows.
        bounds = (0.0, 0.0, 300_500.0, 200_100.0)
        grid, transform = get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=bounds)
        assert grid.shape == (201, 301)

    def test_grid_is_nan_filled_float64_reference_only(self):
        bounds = (0.0, 0.0, 10_000.0, 10_000.0)
        grid, _ = get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=bounds)
        assert grid.dtype == np.float64
        assert np.isnan(grid).all()

    def test_transform_places_origin_at_bounds_minx_maxy(self):
        bounds = (100_000.0, 200_000.0, 110_000.0, 215_000.0)
        _, transform = get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=bounds)
        assert transform.c == pytest.approx(bounds[0])  # minx
        assert transform.f == pytest.approx(bounds[3])  # maxy (origin is top-left)

    def test_pixels_are_square_no_aea_distortion_within_one_region(self):
        """The grid is built from a single meter-based AEA CRS bounding box,
        so pixel width and height must match exactly regardless of region
        shape -- this is what lets raster cell counts convert directly to
        km2 without a separate area-correction step (04_spatial_methodology.md)."""
        bounds = (0.0, 0.0, 50_000.0, 30_000.0)
        _, transform = get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=bounds)
        assert abs(transform.a) == pytest.approx(abs(transform.e), rel=1e-9)

    def test_both_regions_produce_valid_grids_with_real_config(self):
        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            bounds = (0.0, 0.0, 20_000.0, 20_000.0)
            grid, transform = get_analysis_grid(region, CONFIG, bounds=bounds)
            assert grid.shape == (20, 20)
            assert not np.isnan(transform.a)


class TestGetAnalysisGridValidation:
    def test_non_region_enum_raises(self):
        with pytest.raises(ValueError):
            get_analysis_grid("brazil", CONFIG, bounds=(0.0, 0.0, 1000.0, 1000.0))

    def test_crs_mismatch_between_config_and_regioncrs_raises(self):
        mismatched_config = _fake_config(grid_crs="+proj=longlat +datum=WGS84 +no_defs")
        with pytest.raises(ValueError, match="CRS mismatch"):
            get_analysis_grid(Region.NORDESTE_BR, mismatched_config, bounds=(0.0, 0.0, 1000.0, 1000.0))

    def test_matching_crs_does_not_raise(self):
        matching_config = _fake_config(grid_crs=RegionCRS.projected_crs_for(Region.NORDESTE_BR))
        grid, _ = get_analysis_grid(Region.NORDESTE_BR, matching_config, bounds=(0.0, 0.0, 1000.0, 1000.0))
        assert grid.shape == (1, 1)

    def test_inverted_bounds_produce_non_positive_dimensions_and_raise(self):
        with pytest.raises(ValueError):
            get_analysis_grid(Region.NORDESTE_BR, CONFIG, bounds=(1000.0, 1000.0, 0.0, 0.0))


class TestResolveResampling:
    def test_known_methods_resolve(self):
        for name in ("bilinear", "nearest", "average", "mode", "cubic"):
            assert _resolve_resampling(name).name.lower() == name

    def test_unknown_method_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="nearest.*mode|mode.*nearest"):
            _resolve_resampling("magic")


class TestReprojectAndResample:
    def test_missing_source_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            reproject_and_resample(
                tmp_path / "does_not_exist.tif",
                target_crs=RegionCRS.projected_crs_for(Region.NORTH_GERMANY),
            )

    def test_geographic_target_crs_raises(self, tmp_path):
        source = _write_raster(
            tmp_path / "source.tif",
            bounds=(0.0, 0.0, 10_000.0, 10_000.0),
            crs=RegionCRS.projected_crs_for(Region.NORDESTE_BR),
        )
        with pytest.raises(ValueError, match="not a projected CRS"):
            reproject_and_resample(source, target_crs="EPSG:4326")

    def test_reprojects_between_the_two_study_regions_projected_crs(self, tmp_path):
        """A genuine reprojection between the two real AEA definitions used
        in this pipeline (not a trivial identity transform): the two
        projections have different origins/orientations, so a square
        source footprint warps into a non-square footprint in destination
        pixel space. Proof this is a real geometric warp, not a CRS-tag
        relabeling of an unchanged array: SOME destination pixels sample
        the source value, but NOT ALL of them do (the corners fall outside
        the warped footprint) -- a naive relabel would leave every pixel
        at the fill value."""
        source = _write_raster(
            tmp_path / "source.tif",
            bounds=(0.0, 0.0, 10_000.0, 10_000.0),
            crs=RegionCRS.projected_crs_for(Region.NORDESTE_BR),
            fill_value=42.0,
        )
        result = reproject_and_resample(
            source, target_crs=RegionCRS.projected_crs_for(Region.NORTH_GERMANY), method="nearest",
        )
        assert result.size > 0
        assert np.any(np.isclose(result, 42.0))
        assert not np.all(np.isclose(result, 42.0))
