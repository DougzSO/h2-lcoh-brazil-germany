"""VIKOR (Visekriterijumska Optimizacija I Kompromisno Resenje) spatial
suitability scoring (METHODOLOGY.md 2.3): an independent robustness
cross-check against topsis.py's ranking, confirming the suitability result
is not an artefact of the specific MCDA method chosen. Runs independently
per region and per renewable technology on the 1 km analysis grid,
restricted to cells not excluded by exclusion_mask.create_exclusion_mask().
Writes data/processed/vikor_suitability_<region>_<tech>.tif.

REUSE NOTE: criteria loading, grid alignment, and weight resolution are
NOT re-implemented here. This module imports topsis.py's CRITERIA,
CRITERION_DIRECTION, _load_criterion(), and _get_default_weights()
directly, so VIKOR scores the exact same four criteria (resource, slope,
distance_to_grid, distance_to_water), on the exact same reference-grid
alignment (see topsis.py's ALIGNMENT NOTE), under the exact same
config.topsis.weights mapping (see topsis.py's WEIGHT-FIELD MAPPING NOTE)
as TOPSIS -- a precondition for the concordance check below to mean
anything (comparing two methods that silently disagree on their inputs
would not test the method, only the input drift). exclusion_mask.py and
data_layers.py are called as-is, unmodified.

VIKOR ALGORITHM: for each criterion j, f*_j and f-_j are the best/worst
values among valid cells (max/min swapped for cost vs. benefit criteria,
mirroring TOPSIS's ideal/anti-ideal). Normalized distance from the ideal:
d_ij = (f*_j - x_ij) / (f*_j - f-_j) -- in [0, 1] regardless of criterion
direction, since numerator and denominator sign flip together for cost
criteria. Group utility S_i = sum_j(w_j * d_ij); individual regret
R_i = max_j(w_j * d_ij). Compromise index:
    Q_i = v * (S_i - S*) / (S- - S*) + (1-v) * (R_i - R*) / (R- - R*)
with v=0.5 (equal weight to group utility and individual regret), S*/S-
the min/max of S across valid cells, R*/R- the min/max of R. Q_i in [0, 1],
0 = best. Converted to the module's suitability scale via
suitability_i = 1.0 - Q_i, so 1.0 = best -- matching TOPSIS's [0, 1],
higher-is-better convention, which is what makes the two rasters directly
comparable in compute_concordance() below.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from scipy.stats import spearmanr

from src.core.constants import Region, RegionCRS
from src.spatial import data_layers
from src.spatial.exclusion_mask import create_exclusion_mask
from src.spatial.grid_utils import get_analysis_grid
from src.spatial.topsis import CRITERIA, CRITERION_DIRECTION, _get_default_weights, _load_criterion

PROCESSED_DIR = Path("data/processed")


class VikorError(RuntimeError):
    """Raised when VIKOR scoring cannot be completed for a region/tech pair."""


def _vectorized_vikor(
    matrix: np.ndarray, weights: np.ndarray, directions: Tuple[str, ...], v: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized VIKOR over an (N, K) decision matrix. Returns (Q, S, R),
    each shape (N,). Q is the compromise index (0=best); S is group
    utility, R is individual regret (returned for diagnostics/metadata,
    not required by callers)."""
    f_star = np.empty(matrix.shape[1])
    f_minus = np.empty(matrix.shape[1])
    for j, direction in enumerate(directions):
        if direction == "benefit":
            f_star[j], f_minus[j] = matrix[:, j].max(), matrix[:, j].min()
        else:  # cost
            f_star[j], f_minus[j] = matrix[:, j].min(), matrix[:, j].max()

    denom = f_star - f_minus
    denom_safe = np.where(denom == 0, 1.0, denom)
    d = (f_star - matrix) / denom_safe
    d = np.where(denom == 0, 0.0, d)  # constant column -> no discrimination

    S = (d * weights).sum(axis=1)
    R = (d * weights).max(axis=1)

    S_star, S_minus = S.min(), S.max()
    R_star, R_minus = R.min(), R.max()

    S_term = (S - S_star) / (S_minus - S_star) if S_minus != S_star else np.zeros_like(S)
    R_term = (R - R_star) / (R_minus - R_star) if R_minus != R_star else np.zeros_like(R)

    Q = v * S_term + (1.0 - v) * R_term
    return Q, S, R


def run_vikor(
    region: Region,
    tech: str,
    config,
    v: float = 0.5,
    custom_weights: Optional[Dict[str, float]] = None,
    verbose: bool = False,
) -> Tuple[np.ndarray, Dict]:
    """Run VIKOR suitability scoring for a region + renewable technology.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind" -- selects the resource criterion source, same
        as run_topsis().
    config : ScenarioConfig
    v : float
        Compromise weight balancing group utility (S) against individual
        regret (R) in Q_i. Default 0.5 (equal weight), per METHODOLOGY.md.
    custom_weights : Optional[Dict[str, float]]
        Weights dict keyed by topsis.CRITERIA, summing to 1.0. Defaults to
        topsis._get_default_weights() -- the SAME resolution TOPSIS uses,
        so pass the same custom_weights (e.g. from
        topsis.perturb_topsis_weights()) to both for a like-for-like
        sensitivity comparison.
    verbose : bool
        If True, print per-criterion shape/CRS/reprojection/min-mean-max
        detail plus the intermediate valid-cell-count line, for debugging.
        Default False: only the single final summary line prints (method,
        region, tech, valid cells, score range, elapsed time).

    Returns
    -------
    (scores, metadata) : Tuple[np.ndarray, Dict]
        scores is float32, shape matching get_analysis_grid(region, config);
        excluded/masked cells are 0.0 (same convention as run_topsis() --
        note a genuinely valid but maximally-poor cell can also legitimately
        score 0.0, so a 0.0 pixel alone does not distinguish "excluded"
        from "scored worst"; cross-reference the exclusion mask directly if
        that distinction matters).
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
        raise VikorError(
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

    # Decision matrix restricted to valid cells only (same memory
    # optimization as run_topsis() -- VIKOR never runs over excluded pixels).
    matrix = np.column_stack([criteria_arrays[c][valid] for c in CRITERIA])
    weights_arr = np.array([weights[c] for c in CRITERIA])
    directions = tuple(CRITERION_DIRECTION[c] for c in CRITERIA)

    Q, S, R = _vectorized_vikor(matrix, weights_arr, directions, v)
    suitability_valid = 1.0 - Q
    if verbose:
        print(
            f"  VIKOR suitability: min={suitability_valid.min():.4f} mean={suitability_valid.mean():.4f} "
            f"max={suitability_valid.max():.4f} (Q min={Q.min():.4f} max={Q.max():.4f})"
        )

    full_scores = np.zeros(out_shape, dtype=np.float32)
    full_scores[valid] = suitability_valid.astype(np.float32)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"vikor_suitability_{region.value}_{tech}.tif"
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
        "v": v,
        "weights_used": weights,
        "valid_cells": n_valid,
        "total_cells": int(mask.size),
        "score_min": float(suitability_valid.min()),
        "score_mean": float(suitability_valid.mean()),
        "score_max": float(suitability_valid.max()),
        "output_path": str(out_path),
    }

    print(
        f"[vikor] {region.value}/{tech}: {n_valid} valid cells, "
        f"score range [{suitability_valid.min():.3f}, {suitability_valid.max():.3f}], "
        f"{time.time() - t0:.1f}s -> {out_path.name}"
    )
    return full_scores, metadata


def compute_concordance(
    topsis_scores: np.ndarray, vikor_scores: np.ndarray, mask: np.ndarray, top_k_pct: float = 0.10
) -> Dict[str, float]:
    """Compare a TOPSIS and a VIKOR suitability raster for the same
    region/tech, restricted to mask==1 cells.

    Two agreement measures, per METHODOLOGY.md 2.3/2.7's robustness-check
    intent:
    - Spearman rank correlation (scipy.stats.spearmanr) over the full
      mask==1 population -- do the two methods rank cells similarly
      overall?
    - Jaccard similarity of the top `top_k_pct` cells by each method
      (|intersection| / |union| of the two top-k index sets, as a
      percentage) -- do the two methods agree on WHICH cells are best,
      not just on the overall ranking correlation? A high Spearman rho
      with low top-k overlap would indicate the methods agree broadly but
      diverge exactly where site selection cares most.

    Parameters
    ----------
    topsis_scores, vikor_scores : np.ndarray
        Same shape, e.g. the first return value of run_topsis()/run_vikor()
        for the same region/tech.
    mask : np.ndarray
        Same shape, 1=include/0=exclude (typically the exclusion mask
        itself). NOTE: this is coarser than the "valid" finite-criteria
        subset run_topsis()/run_vikor() use internally -- a mask==1 cell
        that either function treated as invalid (non-finite criterion) is
        included here with its stored 0.0 score from both rasters, which
        biases neither method's score relative to the other but can
        slightly inflate the reported concordance via tied zeros. If exact
        precision matters, derive `mask` from the same finite-criteria
        check used internally rather than the raw exclusion mask.
    top_k_pct : float
        Fraction (e.g. 0.10 for 10%) of the mask==1 population to compare
        for the top-k overlap measure.

    Returns
    -------
    Dict[str, float]
        {"spearman_rho": ..., "spearman_pvalue": ..., "top_k_overlap_pct": ...}

    Raises
    ------
    ValueError
        If shapes mismatch, fewer than 2 valid cells are found, or
        top_k_pct is not in (0, 1].
    """
    if topsis_scores.shape != vikor_scores.shape or topsis_scores.shape != mask.shape:
        raise ValueError(
            f"Shape mismatch: topsis={topsis_scores.shape}, vikor={vikor_scores.shape}, "
            f"mask={mask.shape} -- all three must match."
        )
    if not (0.0 < top_k_pct <= 1.0):
        raise ValueError(f"top_k_pct must be in (0, 1], got {top_k_pct}")

    valid = mask == 1
    t = topsis_scores[valid]
    r = vikor_scores[valid]
    if t.size < 2:
        raise ValueError(f"Need at least 2 mask==1 cells to compute concordance, got {t.size}")

    rho, pvalue = spearmanr(t, r)

    k = max(1, int(round(top_k_pct * t.size)))
    topsis_top = set(np.argsort(t)[-k:])
    vikor_top = set(np.argsort(r)[-k:])
    intersection = len(topsis_top & vikor_top)
    union = len(topsis_top | vikor_top)
    top_k_overlap_pct = 100.0 * intersection / union if union > 0 else 0.0

    return {
        "spearman_rho": float(rho),
        "spearman_pvalue": float(pvalue),
        "top_k_overlap_pct": top_k_overlap_pct,
    }
