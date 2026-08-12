"""Zonal aggregation of the TOPSIS suitability raster into ranked candidate
hydrogen-hub sites (METHODOLOGY.md 2.3): identifies contiguous clusters of
suitable cells, discards clusters below the minimum contiguous-area
threshold, aggregates mean/max suitability and suitable area per site, and
ranks the survivors. Writes
data/processed/candidate_sites_<region>_<tech>_baseline.{geojson,csv}.

FILENAME NOTE: the `_baseline` suffix exists for naming consistency with
`h2_potential_{region}_{tech}_{scenario}.*` (which genuinely varies by
combined scenario), NOT because this module's own output varies -- TOPSIS
suitability, VIKOR, and site selection consume only raw resource/slope/
distance rasters and the land-use/protected-area/boundary exclusion mask,
never capacity_factor/capacity_density/WACC, so this module is
scenario-invariant by construction. There are no `_min`/`_max` variants of
this file; only `_baseline` is ever written.

INPUT NOTE: reads topsis_suitability_{region}_{tech}.tif and
exclusion_mask_{region}.tif directly from data/processed/ (both already
written to disk by topsis.run_topsis() / exclusion_mask.create_exclusion_mask()
-- Hard Rule 7, root CLAUDE.md: "Each stage writes to disk before next
stage reads."), rather than re-running either. Neither module's logic is
imported or modified.

ZONAL-UNIT NOTE: admin_boundaries.py's own module docstring documents this
module as a consumer of get_dissolved_polygon() ("site_selection.py -> uses
get_dissolved_polygon() for zonal statistics"), which by design always
returns a SINGLE dissolved polygon per region (9 Brazilian states / 2
German Bundesländer dissolved into one outline each -- see
admin_boundaries.py's own docstring). This module implements both branches
the task's specification describes -- per-administrative-unit zonal_stats
if the boundary source ever exposes more than one feature, and per-
contiguous-cluster aggregation (vectorizing each surviving raster cluster
into its own polygon via rasterio.features.shapes) otherwise -- but only
the second branch is exercised today, since get_dissolved_polygon() always
returns exactly one row. A future admin_boundaries_fetch.py enhancement
fetching municipality/Landkreis-level geometries (the granularity
METHODOLOGY.md 2.3 describes) would activate the first branch without
requiring changes here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes as rasterio_shapes
from scipy import ndimage
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

from src.core.constants import Region, RegionCRS
from src.spatial.admin_boundaries import get_dissolved_polygon

PROCESSED_DIR = Path("data/processed")

OUTPUT_COLUMNS = [
    "rank", "site_id", "region", "tech", "mean_suitability",
    "max_suitability", "suitable_area_km2", "geometry",
]

# Candidate name fields on a per-unit boundary source, tried in order. Not
# exercised today (see module ZONAL-UNIT NOTE) but kept for the
# per-administrative-unit branch. Falls back to a positional label.
_LABEL_FIELD_CANDIDATES = ("NAME_1", "name", "nome", "NM_UF", "SIGLA_UF", "sigla")


class SiteSelectionError(RuntimeError):
    """Raised when candidate sites cannot be selected for a region/tech pair."""


def _empty_gdf(target_crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {c: [] for c in OUTPUT_COLUMNS if c != "geometry"},
        geometry=gpd.GeoSeries([], crs=target_crs), crs=target_crs,
    )[OUTPUT_COLUMNS]


def _label_for(row, idx: int) -> str:
    for field in _LABEL_FIELD_CANDIDATES:
        if field in row and row[field]:
            return str(row[field])
    return f"unit_{idx}"


def _rank_and_finalize(rows: list, target_crs: str) -> gpd.GeoDataFrame:
    if not rows:
        return _empty_gdf(target_crs)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=target_crs)
    rank_score = gdf["mean_suitability"] * np.log1p(gdf["suitable_area_km2"])
    gdf = gdf.loc[rank_score.sort_values(ascending=False).index].reset_index(drop=True)
    gdf.insert(0, "rank", np.arange(1, len(gdf) + 1))
    return gdf[OUTPUT_COLUMNS]


def select_candidate_sites(
    region: Region, tech: str, config, top_n: Optional[int] = None,
    top_suitability_percentile: float = 0.85, verbose: bool = False,
) -> gpd.GeoDataFrame:
    """Aggregate a region/tech's TOPSIS suitability raster into ranked
    candidate hydrogen-hub sites.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind" -- must match a previously run
        topsis.run_topsis(region, tech, config) output on disk.
    config : ScenarioConfig
        config.thresholds.min_contiguous_suitable_area_km2 and
        .max_contiguous_suitable_area_km2 are read -- never hardcoded
        (Hard Rule 4, root CLAUDE.md).
    top_n : Optional[int]
        If given, keep only the top_n ranked sites in the returned/written
        result.
    top_suitability_percentile : float
        Only cells at or above this percentile of the region/tech's valid
        (mask==1, suitability>0) suitability distribution are eligible for
        clustering; default 0.85 keeps the top 15%. Without this, TOPSIS
        rasters where most valid cells score above ~0.9 (common when the
        underlying resource layer is fairly uniform across a large area,
        e.g. Northeast Brazil's 9-state bbox) cluster into amorphous,
        bioma-scale "sites" spanning millions of km2 -- physically
        meaningless for siting GW-scale hydrogen infrastructure, whose real
        footprint is 5-20 km2. Restricting to only the most suitable cells
        breaks these into disconnected, genuinely hub-scale patches before
        clustering even runs.
    verbose : bool
        If True, print intermediate clustering detail (effective
        thresholds, contiguous-cluster count, min-area pass/fail counts,
        capped-cluster count). Default False: only the single final
        summary line prints (sites identified, top cluster ID, top area,
        top score).

    Returns
    -------
    gpd.GeoDataFrame
        Columns: rank, site_id, region, tech, mean_suitability,
        max_suitability, suitable_area_km2, geometry (never dropped).
        Empty (0 rows, correct columns/CRS) if no cluster survives the
        area threshold.
    """
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")
    if not (0.0 <= top_suitability_percentile < 1.0):
        raise ValueError(
            f"top_suitability_percentile must be in [0.0, 1.0), got {top_suitability_percentile}"
        )

    t0 = time.time()

    target_crs = RegionCRS.projected_crs_for(region)
    min_area_km2 = config.thresholds.min_contiguous_suitable_area_km2
    max_area_km2 = config.thresholds.max_contiguous_suitable_area_km2
    if verbose:
        print(
            f"  effective thresholds: top_suitability_percentile="
            f"{top_suitability_percentile:.2f}, min_contiguous_suitable_area_km2="
            f"{min_area_km2}, max_contiguous_suitable_area_km2={max_area_km2}"
        )

    topsis_path = PROCESSED_DIR / f"topsis_suitability_{region.value}_{tech}.tif"
    if not topsis_path.exists():
        raise FileNotFoundError(
            f"TOPSIS suitability raster not found: {topsis_path}. Run "
            f"src.spatial.topsis.run_topsis({region.value!r}, {tech!r}, config) first."
        )
    mask_path = PROCESSED_DIR / f"exclusion_mask_{region.value}.tif"
    if not mask_path.exists():
        raise FileNotFoundError(
            f"Exclusion mask not found: {mask_path}. Run "
            f"src.spatial.exclusion_mask.create_exclusion_mask({region.value!r}, config) first."
        )

    with rasterio.open(topsis_path) as src:
        suitability = src.read(1).astype(np.float64)
        transform = src.transform
        out_shape = suitability.shape
    with rasterio.open(mask_path) as src:
        mask = src.read(1)
    if mask.shape != out_shape:
        raise SiteSelectionError(
            f"Shape mismatch for {region.value!r}: topsis={out_shape}, mask={mask.shape} -- "
            f"both should be built on the same get_analysis_grid() reference grid."
        )

    pixel_area_km2 = abs(transform.a) * abs(transform.e) / 1e6

    # A cell scoring exactly 0.0 is either excluded (mask==0) or the rare
    # genuinely-valid worst-scoring cell (see topsis.py/vikor.py's own
    # documented 0.0 ambiguity) -- requiring suitability > 0 in addition to
    # mask == 1 treats both as non-suitable for clustering purposes, per
    # the task specification.
    suitable = (mask == 1) & (suitability > 0)

    # Restrict clustering to only the top (1 - top_suitability_percentile)
    # share of the valid suitability distribution -- see this function's
    # own docstring for why, without this, a fairly uniform TOPSIS raster
    # over a large region clusters into a single amorphous, bioma-scale
    # "site". top_suitability_percentile=0.0 disables this (keeps the
    # pre-existing >0 behavior exactly).
    if top_suitability_percentile > 0.0 and suitable.any():
        cutoff = np.percentile(suitability[suitable], top_suitability_percentile * 100.0)
        suitable = suitable & (suitability >= cutoff)

    structure = np.ones((3, 3), dtype=int)  # 8-connectivity
    labeled, n_clusters = ndimage.label(suitable, structure=structure)
    if verbose:
        print(f"  found {n_clusters} contiguous suitable clusters (8-connectivity)")

    pixel_counts = np.bincount(labeled.ravel(), minlength=n_clusters + 1)[1: n_clusters + 1]
    cluster_areas_km2 = pixel_counts * pixel_area_km2
    passing_labels = [i + 1 for i, area in enumerate(cluster_areas_km2) if area >= min_area_km2]
    if verbose:
        print(
            f"  {len(passing_labels)} of {n_clusters} clusters pass "
            f"min_contiguous_suitable_area_km2={min_area_km2} km2"
        )

    boundary_gdf = get_dissolved_polygon(region)
    rows = []

    if len(boundary_gdf) > 1:
        # Per-administrative-unit path -- not exercised today, see module
        # ZONAL-UNIT NOTE. Imported lazily: rasterstats is a documented
        # project dependency (ARCHITECTURE.md, root CLAUDE.md) but is not
        # required by the branch that actually runs today, so a missing
        # install there should not block the module from importing/running.
        from rasterstats import zonal_stats

        passing_mask = np.isin(labeled, passing_labels)
        filtered_suitability = np.where(passing_mask, suitability, 0.0)
        stats = zonal_stats(
            boundary_gdf.geometry, filtered_suitability, affine=transform,
            nodata=0.0, stats=["mean", "max", "count"],
        )
        for idx, (row, stat) in enumerate(zip(boundary_gdf.to_dict("records"), stats)):
            count = stat["count"] or 0
            suitable_area_km2 = count * pixel_area_km2
            if suitable_area_km2 == 0:
                continue
            rows.append({
                "site_id": _label_for(row, idx), "region": region.value, "tech": tech,
                "mean_suitability": float(stat["mean"]), "max_suitability": float(stat["max"]),
                "suitable_area_km2": suitable_area_km2, "geometry": row["geometry"],
            })
    else:
        # Single dissolved polygon -- aggregate per contiguous suitable
        # cluster/patch instead, vectorizing each into its own geometry.
        max_area_px = int(max_area_km2 / pixel_area_km2) if max_area_km2 else None
        n_capped = 0
        for label_id in passing_labels:
            cluster_px = labeled == label_id
            count = int(cluster_px.sum())

            # Cap any cluster still exceeding max_contiguous_suitable_area_km2
            # after the top-percentile filter above: shrink it down to its
            # own top max_area_px cells by suitability, rather than dropping
            # it outright -- this is the safety net for a coastal strip or
            # similarly uniform corridor that stays contiguous and huge even
            # after restricting to the top percentile. May split the cluster
            # into several disconnected fragments; unary_union below already
            # handles that (produces a MultiPolygon).
            if max_area_px is not None and count > max_area_px:
                cluster_vals = suitability[cluster_px]
                keep_threshold = np.partition(cluster_vals, -max_area_px)[-max_area_px]
                cluster_px = cluster_px & (suitability >= keep_threshold)
                count = int(cluster_px.sum())
                n_capped += 1

            suitable_area_km2 = count * pixel_area_km2
            if suitable_area_km2 == 0:
                continue
            vals = suitability[cluster_px]
            polys = [
                shapely_shape(geom)
                for geom, val in rasterio_shapes(
                    cluster_px.astype(np.uint8), mask=cluster_px, transform=transform
                )
                if val == 1
            ]
            geometry = unary_union(polys) if polys else None
            if geometry is None:
                continue
            rows.append({
                "site_id": f"cluster_{label_id}", "region": region.value, "tech": tech,
                "mean_suitability": float(vals.mean()), "max_suitability": float(vals.max()),
                "suitable_area_km2": suitable_area_km2, "geometry": geometry,
            })
        if n_capped and verbose:
            print(f"  {n_capped} cluster(s) exceeded max_contiguous_suitable_area_km2={max_area_km2} km2, capped to top-suitability cells")

    result = _rank_and_finalize(rows, target_crs)
    if top_n is not None:
        result = result.iloc[:top_n].reset_index(drop=True)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # "_baseline" suffix for naming consistency with h2_potential_*'s
    # scenario-suffixed outputs -- this module's own output is
    # scenario-invariant (see module docstring's FILENAME NOTE).
    geojson_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.geojson"
    csv_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.csv"

    result.to_file(geojson_path, driver="GeoJSON")
    csv_df = result.copy()
    csv_df["geometry_wkt"] = csv_df["geometry"].apply(lambda g: g.wkt if g is not None else None)
    csv_df.drop(columns="geometry").to_csv(csv_path, index=False)

    # Single final summary line per region/tech: sites identified, top
    # cluster ID/area/score, elapsed time -- replaces the two separate
    # prints (top-ranked site + "wrote ...") this function used to emit.
    if result.empty:
        print(
            f"[site_selection] {region.value}/{tech}: 0 candidate sites survived "
            f"filtering, {time.time() - t0:.1f}s -> {geojson_path.name}"
        )
    else:
        top = result.iloc[0]
        print(
            f"[site_selection] {region.value}/{tech}: {len(result)} site(s), "
            f"top={top['site_id']} (area={top['suitable_area_km2']:.1f} km2, "
            f"score={top['mean_suitability']:.4f}), {time.time() - t0:.1f}s -> {geojson_path.name}"
        )
    return result
