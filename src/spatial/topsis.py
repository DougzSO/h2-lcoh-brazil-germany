"""TOPSIS (Technique for Order of Preference by Similarity to Ideal
Solution) spatial suitability scoring (METHODOLOGY.md 2.3). Runs
independently per region and per renewable technology (solar/wind) on the
1 km analysis grid, restricted to cells not excluded by
exclusion_mask.create_exclusion_mask(). Writes
data/processed/topsis_suitability_<region>_<tech>.tif.

ALIGNMENT NOTE: data_layers.py's loaders (load_solar_irradiance,
load_wind_power_density, load_slope, load_distance_to_grid,
load_distance_to_water) each reproject via grid_utils.reproject_and_resample(),
which computes its OWN output transform from each source raster's
reprojected extent -- not guaranteed to match get_analysis_grid()'s
transform pixel-for-pixel, nor to match each other (see data_layers.py's
CRS NOTE and the same issue exclusion_mask.py already resolved for
land use). This module resolves it the same way: each loaded criterion is
re-read from its metadata["aligned_path"] GeoTIFF and warped (bilinear,
continuous data) directly onto get_analysis_grid()'s exact transform/shape
before assembly into the decision matrix. exclusion_mask.create_exclusion_mask()
already builds its own output on that same reference grid, so the mask
needs no extra alignment step. No changes were made to data_layers.py,
exclusion_mask.py, or grid_utils.py to achieve this.

WEIGHT-FIELD MAPPING NOTE: config.topsis.weights (Pydantic TopsisWeights)
has four fields -- resource_quality, proximity_infrastructure,
land_availability, grid_distance -- whose names do not literally spell the
four criteria this module scores (resource, slope, distance_to_grid,
distance_to_water; see docs/memory/04_spatial_methodology.md's own
"Point to validate" note on this same gap). Mapping used here, in the
absence of a change to scenario_params.yaml (out of scope for this
module):
  resource_quality        -> resource (GHI or wind power density), benefit
  grid_distance            -> distance_to_grid, cost (exact name match)
  proximity_infrastructure -> distance_to_water, cost (water is itself a
                               required electrolysis input, a distinct
                               "infrastructure proximity" concern from the
                               transmission grid)
  land_availability        -> slope, cost (flatter land is more usable/
                               "available" for construction)
Confirm this mapping against the manuscript's intended criteria-to-weight
correspondence before treating a TOPSIS ranking as final.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject as warp_reproject

from src.core.constants import Region, RegionCRS
from src.spatial import data_layers
from src.spatial.exclusion_mask import create_exclusion_mask
from src.spatial.grid_utils import get_analysis_grid

PROCESSED_DIR = Path("data/processed")

CRITERIA = ("resource", "slope", "distance_to_grid", "distance_to_water")

# "benefit" = higher is more suitable, "cost" = lower is more suitable
CRITERION_DIRECTION = {
    "resource": "benefit",
    "slope": "cost",
    "distance_to_grid": "cost",
    "distance_to_water": "cost",
}


class TopsisError(RuntimeError):
    """Raised when TOPSIS scoring cannot be completed for a region/tech pair."""


def _align_to_grid(aligned_path: Path, target_crs: str, out_shape: tuple, transform) -> np.ndarray:
    """Warp a data_layers-aligned raster onto the reference grid's exact
    transform/shape (bilinear, continuous data). See module ALIGNMENT NOTE."""
    with rasterio.open(aligned_path) as src:
        dst = np.full(out_shape, np.nan, dtype=np.float64)
        warp_reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=target_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    return dst


def _load_criterion(
    loader_fn, region, config, target_crs, out_shape, transform, label: str, verbose: bool = False
) -> np.ndarray:
    """Load and grid-align one criterion. `verbose=True` prints per-criterion
    shape/CRS/reprojection/min-mean-max detail; default False keeps this
    silent so run_topsis()/run_vikor() emit only their own single summary
    line per region/tech (see those functions)."""
    t0 = time.time()
    _, metadata = loader_fn(region, config)
    aligned = _align_to_grid(Path(metadata["aligned_path"]), target_crs, out_shape, transform)
    if verbose:
        finite_vals = aligned[np.isfinite(aligned)]
        stats = (
            f"min={finite_vals.min():.2f} mean={finite_vals.mean():.2f} max={finite_vals.max():.2f}"
            if finite_vals.size else "no finite pixels"
        )
        print(f"  {label}: aligned onto {out_shape} grid in {time.time() - t0:.1f}s ({stats})")
    return aligned


def _get_default_weights(config) -> Dict[str, float]:
    """Resolve the default weights dict (keyed by CRITERIA) from
    config.topsis.weights. See module WEIGHT-FIELD MAPPING NOTE."""
    w = config.topsis.weights
    return {
        "resource": w.resource_quality,
        "distance_to_grid": w.grid_distance,
        "distance_to_water": w.proximity_infrastructure,
        "slope": w.land_availability,
    }


def perturb_topsis_weights(
    weights: Dict[str, float], target_criterion: str, delta_pct: float
) -> Dict[str, float]:
    """Perturb `target_criterion`'s weight by `delta_pct` (e.g. +0.20 / -0.20)
    and proportionately renormalize the remaining weights so the full set
    still sums to 1.0. Used by sensitivity/sensitivity_analysis.py's
    weight-perturbation-vs-VIKOR concordance check.

    Parameters
    ----------
    weights : Dict[str, float]
        A full weights dict keyed by criterion name (see CRITERIA), summing
        to 1.0 -- e.g. the output of _get_default_weights().
    target_criterion : str
        Must be a key in `weights`.
    delta_pct : float
        Fractional change applied to target_criterion's weight, e.g. 0.20
        for +20%. The result is clamped to [0, 1] before renormalization.

    Returns
    -------
    Dict[str, float]
        New weights dict, same keys, summing to 1.0 (up to float precision).
    """
    if target_criterion not in weights:
        raise ValueError(
            f"Unknown criterion {target_criterion!r}; expected one of {sorted(weights)}"
        )

    new_target = min(max(weights[target_criterion] * (1.0 + delta_pct), 0.0), 1.0)
    remaining_keys = [k for k in weights if k != target_criterion]
    remaining_total_old = sum(weights[k] for k in remaining_keys)
    remaining_total_new = 1.0 - new_target

    perturbed = {target_criterion: new_target}
    if remaining_total_old > 0:
        scale = remaining_total_new / remaining_total_old
        for k in remaining_keys:
            perturbed[k] = weights[k] * scale
    else:
        share = remaining_total_new / len(remaining_keys) if remaining_keys else 0.0
        for k in remaining_keys:
            perturbed[k] = share

    return perturbed


def _vectorized_topsis(
    matrix: np.ndarray, weights: np.ndarray, directions: Tuple[str, ...],
    norm_method: str = "vector", cv_threshold: float = 0.10,
    criterion_names: Optional[Tuple[str, ...]] = None,
) -> np.ndarray:
    """Vectorized TOPSIS over an (N, K) decision matrix. Returns the (N,)
    relative-closeness score C_i in [0, 1] for each row.

    norm_method="vector" (default, the published method -- METHODOLOGY.md
    2.3) always vector-normalizes every column, unchanged from before this
    parameter existed. norm_method="adaptive" is a robustness-check-only
    variant (see TopsisNormalization in config_loader.py): per column,
    computes CV = std/mean and uses min-max normalization when
    CV < cv_threshold (a criterion too spatially uniform for vector
    normalization to discriminate on), vector normalization otherwise.
    """
    r = np.empty_like(matrix, dtype=np.float64)
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        mean = col.mean()
        cv = abs(col.std() / mean) if mean != 0 else np.inf
        use_minmax = norm_method == "adaptive" and cv < cv_threshold
        name = criterion_names[j] if criterion_names else f"criterion_{j}"
        if use_minmax:
            col_min, col_max = col.min(), col.max()
            r[:, j] = 0.5 if col_max == col_min else (col - col_min) / (col_max - col_min)
            print(f"[topsis] {name}: CV={cv:.4f} < {cv_threshold} -> min-max normalization")
        else:
            norm = np.sqrt((col ** 2).sum())
            r[:, j] = col / norm if norm != 0 else 0.0
            if norm_method == "adaptive":
                print(f"[topsis] {name}: CV={cv:.4f} >= {cv_threshold} -> vector normalization")
    v = r * weights

    ideal_pos = np.empty(matrix.shape[1])
    ideal_neg = np.empty(matrix.shape[1])
    for j, direction in enumerate(directions):
        if direction == "benefit":
            ideal_pos[j], ideal_neg[j] = v[:, j].max(), v[:, j].min()
        else:  # cost
            ideal_pos[j], ideal_neg[j] = v[:, j].min(), v[:, j].max()

    s_pos = np.sqrt(((v - ideal_pos) ** 2).sum(axis=1))
    s_neg = np.sqrt(((v - ideal_neg) ** 2).sum(axis=1))
    denom = s_pos + s_neg
    return np.where(denom > 0, s_neg / np.where(denom > 0, denom, 1.0), 0.5)


def run_topsis(
    region: Region,
    tech: str,
    config,
    custom_weights: Optional[Dict[str, float]] = None,
    verbose: bool = False,
    norm_method: Optional[str] = None,
) -> Tuple[np.ndarray, Dict]:
    """Run TOPSIS suitability scoring for a region + renewable technology.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind" -- selects the resource_quality criterion source.
    config : ScenarioConfig
    custom_weights : Optional[Dict[str, float]]
        Weights dict keyed by CRITERIA, summing to 1.0. Defaults to
        config.topsis.weights via _get_default_weights() (see
        WEIGHT-FIELD MAPPING NOTE). Pass the output of
        perturb_topsis_weights() here for a sensitivity run.
    verbose : bool
        If True, print per-criterion shape/CRS/reprojection/min-mean-max
        detail plus the intermediate valid-cell-count line, for debugging.
        Default False: only the single final summary line prints (method,
        region, tech, valid cells, score range, elapsed time).
    norm_method : Optional[str]
        "vector" or "adaptive" (see _vectorized_topsis). Defaults to
        config.topsis.normalization.method (itself defaulting to "vector"
        if the config field is absent -- read via getattr, never raises).
        ROBUSTNESS-CHECK ONLY: passing norm_method="adaptive" writes to a
        SEPARATE output path (topsis_suitability_{region}_{tech}_adaptive.tif)
        rather than overwriting the primary vector-normalized file that
        site_selection.py/h2_potential.py already consume -- do not wire
        this into the default pipeline run.

    Returns
    -------
    (scores, metadata) : Tuple[np.ndarray, Dict]
        scores is float32, shape matching get_analysis_grid(region, config);
        excluded/masked cells are 0.0. metadata includes valid-cell counts,
        score summary, weights used, and the output path.
    """
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")

    t0 = time.time()

    target_crs = RegionCRS.projected_crs_for(region)
    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape

    mask, _ = create_exclusion_mask(region, config)

    resource_loader = (
        data_layers.load_solar_irradiance if tech == "solar" else data_layers.load_wind_power_density
    )
    resource = _load_criterion(
        resource_loader, region, config, target_crs, out_shape, transform, "resource", verbose=verbose
    )
    slope = _load_criterion(
        data_layers.load_slope, region, config, target_crs, out_shape, transform, "slope", verbose=verbose
    )
    dist_grid = _load_criterion(
        data_layers.load_distance_to_grid, region, config, target_crs, out_shape, transform,
        "distance_to_grid", verbose=verbose,
    )
    dist_water = _load_criterion(
        data_layers.load_distance_to_water, region, config, target_crs, out_shape, transform,
        "distance_to_water", verbose=verbose,
    )

    criteria_arrays = {
        "resource": resource, "slope": slope, "distance_to_grid": dist_grid, "distance_to_water": dist_water,
    }

    valid = (
        (mask == 1)
        & np.isfinite(resource) & np.isfinite(slope) & np.isfinite(dist_grid) & np.isfinite(dist_water)
    )
    n_valid = int(valid.sum())
    if verbose:
        print(f"  valid unmasked cells: {n_valid} of {mask.size} ({100.0 * n_valid / mask.size:.1f}%)")
    if n_valid == 0:
        raise TopsisError(
            f"No valid unmasked cells for {region.value!r}/{tech!r} -- exclusion mask and "
            f"criteria rasters do not overlap. Check upstream data before proceeding."
        )

    weights = custom_weights if custom_weights is not None else _get_default_weights(config)
    missing = set(CRITERIA) - set(weights)
    if missing:
        raise ValueError(f"weights dict missing criteria: {sorted(missing)}")
    weight_sum = sum(weights[c] for c in CRITERIA)
    if abs(weight_sum - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1.0, got {weight_sum}")

    # Decision matrix restricted to valid cells only (memory optimization --
    # TOPSIS never runs over excluded/invalid pixels).
    matrix = np.column_stack([criteria_arrays[c][valid] for c in CRITERIA])
    weights_arr = np.array([weights[c] for c in CRITERIA])
    directions = tuple(CRITERION_DIRECTION[c] for c in CRITERIA)

    norm_cfg = getattr(getattr(config, "topsis", None), "normalization", None)
    resolved_norm_method = norm_method if norm_method is not None else getattr(norm_cfg, "method", "vector")
    cv_threshold = getattr(norm_cfg, "cv_threshold", 0.10)

    scores_valid = _vectorized_topsis(
        matrix, weights_arr, directions,
        norm_method=resolved_norm_method, cv_threshold=cv_threshold, criterion_names=CRITERIA,
    )
    if verbose:
        print(
            f"  TOPSIS scores: min={scores_valid.min():.4f} mean={scores_valid.mean():.4f} "
            f"max={scores_valid.max():.4f}"
        )

    full_scores = np.zeros(out_shape, dtype=np.float32)
    full_scores[valid] = scores_valid.astype(np.float32)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # "vector" (the primary/published method) keeps the original filename
    # site_selection.py/h2_potential.py already read; any other norm_method
    # writes to its own suffixed path instead of overwriting it.
    suffix = "" if resolved_norm_method == "vector" else f"_{resolved_norm_method}"
    out_path = PROCESSED_DIR / f"topsis_suitability_{region.value}_{tech}{suffix}.tif"
    profile = {
        "driver": "GTiff",
        "height": full_scores.shape[0],
        "width": full_scores.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": target_crs,
        "transform": transform,
        "nodata": None,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(full_scores, 1)

    metadata = {
        "region": region.value,
        "tech": tech,
        "weights_used": weights,
        "norm_method": resolved_norm_method,
        "valid_cells": n_valid,
        "total_cells": int(mask.size),
        "score_min": float(scores_valid.min()),
        "score_mean": float(scores_valid.mean()),
        "score_max": float(scores_valid.max()),
        "output_path": str(out_path),
    }

    print(
        f"[topsis] {region.value}/{tech} ({resolved_norm_method}): {n_valid} valid cells, "
        f"score range [{scores_valid.min():.3f}, {scores_valid.max():.3f}], "
        f"{time.time() - t0:.1f}s -> {out_path.name}"
    )
    return full_scores, metadata


def compute_resource_suitability_correlation(region: Region, tech: str, config, scores: np.ndarray) -> Dict[str, float]:
    """Pearson/Spearman correlation between the region/tech's own resource
    criterion (GHI or wind power density) and an already-computed
    suitability score array of the same shape (typically run_topsis()'s
    own return value) -- used by the adaptive-normalization robustness
    check (run_pipeline.py's stage_spatial()) to compare vector vs.
    adaptive normalization without re-implementing criterion loading or
    the valid-cell mask. Not required by, and does not affect, the
    primary TOPSIS/site_selection/h2_potential path."""
    from scipy.stats import pearsonr, spearmanr

    target_crs = RegionCRS.projected_crs_for(region)
    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape
    mask, _ = create_exclusion_mask(region, config)

    resource_loader = data_layers.load_solar_irradiance if tech == "solar" else data_layers.load_wind_power_density
    resource = _load_criterion(resource_loader, region, config, target_crs, out_shape, transform, "resource")
    valid = (mask == 1) & np.isfinite(resource) & np.isfinite(scores)

    pearson_r, _ = pearsonr(resource[valid], scores[valid])
    spearman_r, _ = spearmanr(resource[valid], scores[valid])
    return {"pearson_r": float(pearson_r), "spearman_r": float(spearman_r)}
