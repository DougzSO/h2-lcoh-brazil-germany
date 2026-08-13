"""Pure-visualization layer (ARCHITECTURE.md's `viz/plotting.py`: "all
plotting functions used across stages"). Reads already-written raster
(`data/processed/*.tif`), vector (`data/processed/*.geojson`,
`data/boundaries/*.geojson`), and table (`outputs/tables/*.csv`) outputs --
never recomputes an analytical result (TOPSIS/VIKOR suitability, LCOH,
sensitivity sweeps) and never imports `src/economics/`,
`src/sensitivity/`, or the analytical modules under `src/spatial/`
(`topsis.py`, `vikor.py`, `exclusion_mask.py`, `site_selection.py`). The
only project import is `src.core.constants` (`Region`/`RegionCRS`).

Economic/sensitivity plot functions (`plot_lcoh_decomposition`,
`plot_sensitivity_tornado`, `plot_incentive_scenarios`) take an
already-computed result (DataFrame/Dict) as an argument rather than
calling an economics module themselves; `plot_competitiveness_frontier`
reads `outputs/tables/competitiveness_frontier_{tech}.csv` by its fixed
path (written by `economics/competitiveness_frontier.py`) -- config is
read directly only for the baseline-WACC annotation, never to recompute
LCOH.

CRS NOTE: GDAL's GeoJSON driver tags any `.geojson` this pipeline writes
in a projected CRS (AEA) as EPSG:4326 per RFC 7946 without reprojecting
the underlying coordinates (verified live -- see `potential/
h2_potential.py`'s CRS ROUND-TRIP NOTE and
`docs/memory/04_spatial_methodology.md`). `_read_vector()` overrides the
CRS with `RegionCRS.projected_crs_for(region)` via
`set_crs(allow_override=True)` rather than trusting the file's tag --
EXCEPT `data/boundaries/{region}.geojson`, which is not written by this
pipeline: Brazil's copy is an offline meters-scale fixture (already AEA,
just mistagged, same as above), but Germany's is a real GADM download in
genuine WGS84 degrees (confirmed live: raw bounds ~6.6-11.6 lon, 51.3-55.1
lat). Blindly relabeling those degrees as AEA meters (the bug this module
had before this fix) placed Germany's boundary a few meters from the AEA
origin -- invisible at the ~300km map extent, which is why Germany's
suitability/candidate-sites maps rendered with no boundary overlay at all.
`_read_vector()` now checks the raw coordinate magnitude (same heuristic
`spatial/admin_boundaries.py` already uses correctly for the same file)
and reprojects via `.to_crs()` only when the raw extent is actually
geographic, instead of applying one fixed assumption to every file.

`matplotlib.use("Agg")` is set immediately after importing `matplotlib`,
before `pyplot` is imported -- headless; `plt.show()` is never called.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib

matplotlib.use("Agg")

import re

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from adjustText import adjust_text
from matplotlib_scalebar.scalebar import ScaleBar

from src.core.constants import Region, RegionCRS

plt.rcParams.update(
    {
        "font.size": 11,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

MAPS_DIR = Path("outputs/maps")
FIGURES_DIR = Path("outputs/figures")
PROCESSED_DIR = Path("data/processed")
BOUNDARIES_DIR = Path("data/boundaries")
TABLES_DIR = Path("outputs/tables")

DPI = 300
EXCLUDED_COLOR = "#d3d3d3"
RENEWABLE_TECHS = ("solar_pv", "onshore_wind")

REGION_LABELS = {"brazil": "Brazil (Northeast)", "germany": "Germany (North)"}
TECH_LABELS = {"solar": "Solar PV", "wind": "Onshore Wind"}
# Resource-criterion CV (GHI), independently verified against the real
# loaded rasters (see docs/memory/04_spatial_methodology.md's "Adaptive
# normalization" section) -- both well under 6%, unlike wind's 24-73%.
SOLAR_GHI_CV_NOTE = (
    "Spatial variability is driven by infrastructure criteria "
    "(slope/grid/water distance), not resource quality: GHI has CV < 6% here."
)


def _map_title(region: Region, tech: str) -> str:
    return f"{REGION_LABELS[region.value]} — {TECH_LABELS[tech]}"


def _projection_caption(region: Region) -> str:
    """One-line caption citing this region's Albers Equal Area center,
    parsed directly from RegionCRS's own PROJ4 string rather than
    hardcoded, so it can never drift from the CRS actually used."""
    proj4 = RegionCRS.projected_crs_for(region)
    lat0 = re.search(r"\+lat_0=(\S+)", proj4).group(1)
    lon0 = re.search(r"\+lon_0=(\S+)", proj4).group(1)
    return f"Albers Equal Area, center ({lat0}°N, {lon0}°E)"


def _add_map_decorations(ax, scalebar_location: str = "lower left") -> None:
    """Scale bar + a discreet north arrow (upper right), shared by every
    map in this module. `scalebar_location` defaults to lower left but is
    overridable (candidate-sites maps already put the marker-size legend
    there, so those calls use "lower right" instead)."""
    ax.add_artist(ScaleBar(1, units="m", location=scalebar_location, length_fraction=0.2))
    ax.annotate(
        "N", xy=(0.95, 0.98), xytext=(0.95, 0.85), xycoords="axes fraction",
        ha="center", va="center", fontsize=11, fontweight="bold",
        arrowprops=dict(facecolor="black", width=3, headwidth=9, headlength=8),
    )


def _ensure_dirs() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)


def _read_vector(path: Path, region: Region) -> gpd.GeoDataFrame:
    """Read a `.geojson`, correcting for GDAL's GeoJSON CRS-tagging quirk
    (see module CRS NOTE) -- but only override the CRS tag directly when
    the raw coordinates are already in projected (AEA meters) magnitude;
    reproject via `.to_crs()` when they are genuinely geographic (degrees),
    same detection `spatial/admin_boundaries.py` uses for this same file."""
    gdf = gpd.read_file(path)
    target_crs = RegionCRS.projected_crs_for(region)
    minx, miny, maxx, maxy = gdf.total_bounds
    is_geographic = -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90
    if is_geographic:
        gdf = gdf.set_crs("EPSG:4326", allow_override=True).to_crs(target_crs)
    elif str(gdf.crs) != str(target_crs):
        gdf = gdf.set_crs(target_crs, allow_override=True)
    return gdf


def _save_fig(fig, out_path: Path) -> Path:
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"[plotting] wrote {out_path}")
    return out_path


def plot_suitability_map(region: Region, tech: str, config, output_suffix: str = "") -> Path:
    """Suitability heatmap for one region/tech pair, from
    `topsis_suitability_{region}_{tech}.tif` + `exclusion_mask_{region}.tif`
    (written by `spatial/topsis.py` / `spatial/exclusion_mask.py`, neither
    re-run nor re-derived here).

    Color scale: vmin/vmax are the 2nd/98th percentile of the raster's own
    valid (unmasked) values, not a fixed [0, 1] range. TOPSIS scores are
    bounded in [0, 1] by construction, but the actual valid-cell range is
    typically much narrower (e.g. ~0.05-0.999) -- a fixed [0, 1] scale (or,
    equivalently, imshow's own default autoscale, which is dragged toward 0
    by leftover 0.0-valued cells inside the exclusion mask's "suitable"
    footprint that TOPSIS itself never scored, see topsis.py's `valid` mask)
    compresses that real variation into a near-uniform color and hides the
    actual spatial signal. Percentile clipping is a display-only choice:
    it never touches the underlying `.tif`, so it does not affect any
    downstream analysis reading that file.

    `output_suffix` is appended to the output filename before `.png` (e.g.
    "_v2"), for side-by-side comparison against a previously generated map
    with the same region/tech under a different rendering; the default ""
    reproduces the standard `suitability_{region}_{tech}.png` path.
    """
    _ensure_dirs()
    topsis_path = PROCESSED_DIR / f"topsis_suitability_{region.value}_{tech}.tif"
    mask_path = PROCESSED_DIR / f"exclusion_mask_{region.value}.tif"
    if not topsis_path.exists():
        raise FileNotFoundError(f"Suitability raster not found: {topsis_path}")
    if not mask_path.exists():
        raise FileNotFoundError(f"Exclusion mask not found: {mask_path}")

    with rasterio.open(topsis_path) as src:
        suitability = src.read(1)
        bounds = src.bounds
    with rasterio.open(mask_path) as src:
        mask = src.read(1)

    plot_arr = np.ma.masked_where(mask != 1, suitability)
    cmap = matplotlib.colormaps["viridis"].copy()
    cmap.set_bad(EXCLUDED_COLOR)

    valid_values = plot_arr.compressed()
    if valid_values.size:
        vmin, vmax = np.nanpercentile(valid_values, [2, 98])
    else:
        vmin, vmax = None, None

    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(
        plot_arr, cmap=cmap, vmin=vmin, vmax=vmax,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
        origin="upper",
    )
    fig.colorbar(im, ax=ax, label="TOPSIS suitability (2nd-98th pct.)", shrink=0.8)

    boundary_path = BOUNDARIES_DIR / f"{region.value}.geojson"
    if boundary_path.exists():
        _read_vector(boundary_path, region).boundary.plot(ax=ax, color="black", linewidth=1.2)
    _add_map_decorations(ax)

    title = f"Site suitability — {_map_title(region, tech)}\n(gray = excluded)"
    caption = _projection_caption(region)
    if tech == "solar":
        caption += f"\n{SOLAR_GHI_CV_NOTE}"
    ax.set_title(title)
    ax.text(0.5, -0.10, caption, transform=ax.transAxes, ha="center", va="top", fontsize=8, color="0.3")
    ax.set_xlabel("Easting (m, AEA projection)")
    ax.set_ylabel("Northing (m, AEA projection)")

    return _save_fig(fig, MAPS_DIR / f"suitability_{region.value}_{tech}{output_suffix}.png")


def _resolve_candidate_sites_path(region: Region, tech: str) -> Optional[Path]:
    """Same file-preference rule plot_candidate_sites_map uses: prefer
    h2_potential_{region}_{tech}_baseline.geojson (adds
    installable_capacity_mw) over candidate_sites_{region}_{tech}_baseline.geojson,
    whichever exists. Both are always read at the "_baseline" scenario
    branch -- these are candidate-site/suitability maps, not economic
    figures, so they show the baseline (not a min/max sweep). Returns None
    if neither file exists yet."""
    h2_path = PROCESSED_DIR / f"h2_potential_{region.value}_{tech}_baseline.geojson"
    sites_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.geojson"
    if h2_path.exists():
        return h2_path
    if sites_path.exists():
        return sites_path
    return None


def compute_shared_suitability_range(tech: str, config) -> Tuple[Optional[float], Optional[float]]:
    """Compute one (vmin, vmax) mean-suitability color range shared across
    BOTH regions for a single renewable technology (e.g. brazil/solar +
    germany/solar together, separately from brazil/wind + germany/wind).

    plot_candidate_sites_map(), called once per region, has no visibility
    into the other region's value range on its own -- left to autoscale
    independently, the same color (e.g. viridis yellow) can represent very
    different absolute suitability scores on Brazil's map vs. Germany's
    map, which is visually misleading when the two are compared side by
    side. Pass this function's result as `vmin`/`vmax` to
    plot_candidate_sites_map() for both regions of the same `tech` to keep
    the color scale, and therefore the meaning of a given color, identical
    across both maps.

    Returns
    -------
    (vmin, vmax) : Tuple[Optional[float], Optional[float]]
        (None, None) if neither region has a candidate-sites file for
        `tech` yet, or neither has any sites -- callers should treat that
        as "fall back to per-map autoscale" (plot_candidate_sites_map's
        own default when vmin/vmax are not given).
    """
    values = []
    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        path = _resolve_candidate_sites_path(region, tech)
        if path is None:
            continue
        gdf = _read_vector(path, region)
        if not gdf.empty and "mean_suitability" in gdf.columns:
            values.append(gdf["mean_suitability"].astype(float).to_numpy())

    if not values:
        return None, None
    combined = np.concatenate(values)
    if combined.size == 0:
        return None, None
    return float(combined.min()), float(combined.max())


def plot_candidate_sites_map(
    region: Region, tech: str, config,
    vmin: Optional[float] = None, vmax: Optional[float] = None,
) -> Path:
    """Candidate hydrogen-hub sites for one region/tech pair, preferring
    `h2_potential_{region}_{tech}_baseline.geojson` (adds
    installable_capacity_mw) over `candidate_sites_{region}_{tech}_baseline.geojson`
    if both exist -- neither `potential/h2_potential.py` nor
    `spatial/site_selection.py`'s logic is re-run. Always the "_baseline"
    scenario branch (a candidate-site map, not an economic figure).

    vmin, vmax : Optional[float]
        Shared mean-suitability color-scale bounds, typically the output of
        compute_shared_suitability_range(tech, config), so this region's
        map and its counterpart region's map for the same `tech` use
        identical colors for identical suitability values. Default None
        falls back to this single map's own autoscale (the previous,
        per-map-only behavior).
    """
    _ensure_dirs()
    source_path = _resolve_candidate_sites_path(region, tech)
    if source_path is None:
        h2_path = PROCESSED_DIR / f"h2_potential_{region.value}_{tech}_baseline.geojson"
        sites_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.geojson"
        raise FileNotFoundError(
            f"Neither {h2_path} nor {sites_path} found -- run "
            f"site_selection.select_candidate_sites() (and optionally "
            f"h2_potential.calculate_h2_potential()) first."
        )

    sites = _read_vector(source_path, region)
    fig, ax = plt.subplots(figsize=(9, 8))

    boundary_path = BOUNDARIES_DIR / f"{region.value}.geojson"
    if boundary_path.exists():
        _read_vector(boundary_path, region).plot(
            ax=ax, facecolor="#f0f0f0", edgecolor="black", linewidth=1.0, zorder=1
        )

    if sites.empty:
        ax.set_title(f"Candidate hydrogen-hub sites — {_map_title(region, tech)}\n(0 sites survived filtering)")
    else:
        size_col = "installable_capacity_mw" if "installable_capacity_mw" in sites.columns else "suitable_area_km2"
        size_values = sites[size_col].astype(float)
        max_val = size_values.max() if size_values.max() > 0 else 1.0
        sizes = 60.0 + 500.0 * (size_values / max_val)

        centroids = sites.geometry.centroid
        scatter = ax.scatter(
            centroids.x, centroids.y, s=sizes, c=sites["mean_suitability"],
            cmap="viridis", vmin=vmin, vmax=vmax, edgecolors="black", linewidths=0.6, zorder=3,
        )
        fig.colorbar(scatter, ax=ax, label="mean TOPSIS suitability", shrink=0.8)

        # adjustText repels overlapping site_id labels apart (with a thin
        # connecting arrow back to the true point) instead of letting dense
        # clusters render as illegible, stacked text.
        texts = [
            ax.text(x, y, str(site_id), fontsize=7)
            for x, y, site_id in zip(centroids.x, centroids.y, sites["site_id"])
        ]
        adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle="-", color="0.4", lw=0.6))

        # Explicit marker-size legend (min/median/max of size_col), rather
        # than only describing the size mapping in the title -- the
        # reference bubbles use a neutral gray fill since color already
        # encodes mean_suitability via the colorbar above.
        unit = "MW" if size_col == "installable_capacity_mw" else "km2"
        ref_vals = sorted(set(
            float(round(v)) for v in np.nanpercentile(size_values, [0, 50, 100])
        ))
        size_handles = [
            ax.scatter(
                [], [], s=60.0 + 500.0 * (val / max_val), color="0.6",
                edgecolors="black", linewidths=0.6, label=f"{val:.0f} {unit}",
            )
            for val in ref_vals
        ]
        size_legend = ax.legend(
            handles=size_handles, title=f"Marker size ~ {size_col}",
            loc="lower left", fontsize=8, framealpha=0.9,
        )
        ax.add_artist(size_legend)

        ax.set_title(f"Candidate hydrogen-hub sites — {_map_title(region, tech)}")

        # Brazil/solar-specific: 19 of 20 sites hit the 2,000 km2 cluster-
        # size cap (see docs/memory/04_spatial_methodology.md), which is why
        # this map's marker sizes barely vary -- flagged here so it isn't
        # mistaken for a rendering issue. Not applicable to the other three
        # region/tech maps (their sites are not uniformly capped).
        if region.value == "brazil" and tech == "solar":
            ax.text(
                0.02, 0.98, "19 of 20 sites capped at 2,000 km² (area selection threshold)",
                transform=ax.transAxes, fontsize=8, color="0.3", ha="left", va="top",
                bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=2),
            )

    _add_map_decorations(ax, scalebar_location="lower right")
    ax.text(0.5, -0.10, _projection_caption(region), transform=ax.transAxes, ha="center", va="top", fontsize=8, color="0.3")
    ax.set_xlabel("Easting (m, AEA projection)")
    ax.set_ylabel("Northing (m, AEA projection)")

    return _save_fig(fig, MAPS_DIR / f"candidate_sites_{region.value}_{tech}.png")


def plot_lcoh_decomposition(decomposition_results: Union[pd.DataFrame, Dict], config) -> Path:
    """Baseline vs. WACC-swap LCOH, grouped by region x renewable
    technology, from an already-computed `decomposition.py`-style result
    table (`actual`/`wacc_swap` rows) -- no LCOH recalculated here."""
    _ensure_dirs()
    df = decomposition_results if isinstance(decomposition_results, pd.DataFrame) else pd.DataFrame(decomposition_results)

    labels, baseline_vals, swap_vals = [], [], []
    for region in ("brazil", "germany"):
        for tech in RENEWABLE_TECHS:
            actual = df[(df.region == region) & (df.renewable_tech == tech) & (df.run_type == "actual")]
            swap = df[(df.region == region) & (df.renewable_tech == tech) & (df.run_type == "wacc_swap")]
            if actual.empty or swap.empty:
                continue
            labels.append(f"{region}\n{tech}")
            baseline_vals.append(float(actual["lcoh_usd_per_kg"].iloc[0]))
            swap_vals.append(float(swap["lcoh_usd_per_kg"].iloc[0]))

    if not labels:
        raise ValueError("decomposition_results has no matching 'actual'/'wacc_swap' rows to plot")

    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.bar(x - width / 2, baseline_vals, width, label="Baseline (actual)", color="#1f77b4")
    ax.bar(x + width / 2, swap_vals, width, label="WACC-swap counterfactual", color="#ff7f0e")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("LCOH (USD/kg H2)")
    ax.set_title("LCOH decomposition: baseline vs. WACC-swap counterfactual")
    ax.legend()

    return _save_fig(fig, FIGURES_DIR / "lcoh_decomposition.png")


def plot_competitiveness_frontier(config, renewable_tech: str) -> Path:
    """2D WACC_BR x WACC_DE competitiveness frontier heatmap for one
    renewable technology, replacing the old plot_inversion_point() (removed
    -- its data source, inversion_points.csv, is no longer produced; see
    ADR-009, docs/memory/06_technical_decisions_log.md). Reads
    `outputs/tables/competitiveness_frontier_{renewable_tech}.csv` (written
    by `economics/competitiveness_frontier.py`'s compute_frontier()) -- no
    LCOH is recomputed here, consistent with this module's "never imports
    src/economics/" rule; only `config` is read directly for the baseline
    WACC annotation (config.regions.<region>.wacc.<tech>.baseline), the
    same pattern plot_suitability_map()/plot_candidate_sites_map() already
    use `config` for.

    delta_lcoh (LCOH_BR - LCOH_DE) is rendered as a diverging heatmap over
    the WACC_BR x WACC_DE grid; grid points where parity_flag is True (the
    5% relative-tolerance band) are outlined; the baseline WACC point for
    both regions is annotated.
    """
    _ensure_dirs()
    frontier_csv = TABLES_DIR / f"competitiveness_frontier_{renewable_tech}.csv"
    if not frontier_csv.exists():
        raise FileNotFoundError(
            f"{frontier_csv} not found -- run "
            f"competitiveness_frontier.run_all_frontiers(config) first."
        )
    df = pd.read_csv(frontier_csv)

    wacc_br_values = np.sort(df["wacc_br"].unique())
    wacc_de_values = np.sort(df["wacc_de"].unique())
    pivot = df.pivot(index="wacc_de", columns="wacc_br", values="delta_lcoh").reindex(
        index=wacc_de_values, columns=wacc_br_values
    )

    fig, ax = plt.subplots(figsize=(9, 7))
    vmax = float(np.nanmax(np.abs(pivot.to_numpy())))
    mesh = ax.pcolormesh(
        wacc_br_values * 100.0, wacc_de_values * 100.0, pivot.to_numpy(),
        cmap="RdBu_r", shading="nearest", vmin=-vmax, vmax=vmax,
    )
    cbar = fig.colorbar(mesh, ax=ax)
    cbar.set_label("ΔLCOH = LCOH_BR − LCOH_DE (USD/kg H2)")

    parity = df[df["parity_flag"]]
    if not parity.empty:
        ax.scatter(
            parity["wacc_br"] * 100.0, parity["wacc_de"] * 100.0,
            facecolors="none", edgecolors="black", linewidths=1.2, s=60,
            label="Parity (±5% relative tolerance)",
        )

    tech_key = "solar" if renewable_tech == "solar_pv" else "wind"
    baseline_br = getattr(getattr(config.regions, "brazil").wacc, tech_key).baseline * 100.0
    baseline_de = getattr(getattr(config.regions, "germany").wacc, tech_key).baseline * 100.0
    ax.scatter([baseline_br], [baseline_de], marker="*", color="black", s=180, zorder=5, label="Baseline WACC")
    ax.annotate(
        f"baseline (BR {baseline_br:.1f}%, DE {baseline_de:.1f}%)",
        (baseline_br, baseline_de), fontsize=8, xytext=(6, 6), textcoords="offset points",
    )

    ax.set_xlabel("Brazil WACC (%)")
    ax.set_ylabel("Germany WACC (%)")
    ax.set_title(f"Competitiveness frontier — {renewable_tech}")
    ax.legend(fontsize=8, loc="upper right")

    return _save_fig(fig, FIGURES_DIR / f"competitiveness_frontier_{renewable_tech}.png")


def plot_sensitivity_tornado(df_sensitivity: pd.DataFrame, region: Region, tech: str) -> Path:
    """Horizontal tornado chart of each swept parameter's LCOH range, from
    an already-computed `sensitivity_analysis.run_sensitivity()` result
    table -- no LCOH is recalculated here. `tech` must match
    `df_sensitivity`'s own `renewable_tech` values ("solar_pv"/
    "onshore_wind"), NOT the spatial siting tech ("solar"/"wind") used by
    `plot_suitability_map`/`plot_candidate_sites_map`."""
    _ensure_dirs()
    subset = df_sensitivity[(df_sensitivity.region == region.value) & (df_sensitivity.renewable_tech == tech)]
    if subset.empty:
        raise ValueError(f"No sensitivity rows for region={region.value!r}, renewable_tech={tech!r}")

    summary = subset.groupby("parameter")["lcoh_result"].agg(["min", "max"]).reset_index()
    summary["range"] = summary["max"] - summary["min"]
    summary = summary.sort_values("range", ascending=True)

    fig, ax = plt.subplots(figsize=(9, 5))
    y = np.arange(len(summary))
    ax.barh(y, summary["range"], left=summary["min"], color="#4c72b0", edgecolor="black")
    ax.set_yticks(y)
    ax.set_yticklabels(summary["parameter"])
    ax.set_xlabel("LCOH (USD/kg H2)")
    ax.set_title(f"Sensitivity tornado -- {region.value} / {tech}")

    return _save_fig(fig, FIGURES_DIR / f"sensitivity_tornado_{region.value}_{tech}.png")


def plot_incentive_scenarios(incentives_results: Union[pd.DataFrame, Dict], config) -> Path:
    """Baseline vs. incentive-adjusted LCOH for both regions (REHIDRO for
    Brazil, IPCEI Hy2Use for Germany), from an already-computed
    `incentive_scenarios.run_all_incentive_scenarios()` result -- no LCOH
    is recalculated here."""
    _ensure_dirs()
    if isinstance(incentives_results, pd.DataFrame):
        data = {row["region"]: row.to_dict() for _, row in incentives_results.iterrows()}
    else:
        data = incentives_results

    brazil, germany = data["brazil"], data["germany"]
    labels = ["Brazil", "Germany"]
    baseline_vals = [brazil["baseline_lcoh_usd_per_kg"], germany["baseline_lcoh_usd_per_kg"]]
    incentive_vals = [brazil["rehidro_lcoh_usd_per_kg"], germany["ipcei_lcoh_usd_per_kg"]]
    incentive_labels = ["REHIDRO", "IPCEI Hy2Use"]

    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.bar(x - width / 2, baseline_vals, width, label="Baseline (no incentive)", color="#1f77b4")
    bars = ax.bar(x + width / 2, incentive_vals, width, label="With incentive", color="#2ca02c")
    for bar, inc_label in zip(bars, incentive_labels):
        ax.annotate(
            inc_label, (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center", va="bottom", fontsize=8,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("LCOH (USD/kg H2)")
    ax.set_title("Incentive scenarios: baseline vs. policy-adjusted LCOH")
    ax.legend()

    return _save_fig(fig, FIGURES_DIR / "incentive_scenarios.png")


def generate_all_plots(config) -> Dict[str, List[Path]]:
    """Run every plotting routine whose prerequisite file(s) already exist
    on disk, skipping (with a warning print, not an exception) any plot
    whose inputs have not been generated yet. Pure orchestration: no
    analytical module is called to fill a gap -- a missing input is always
    a skip, never computed on the fly.

    Returns
    -------
    Dict[str, List[Path]]
        {"maps": [...], "figures": [...]} -- only successfully written paths.
    """
    _ensure_dirs()
    t0 = time.time()
    results: Dict[str, List[Path]] = {"maps": [], "figures": []}

    # Shared mean-suitability color range per technology, computed across
    # BOTH regions up front, so brazil/<tech> and germany/<tech> render
    # with identical colors for identical suitability values (see
    # compute_shared_suitability_range's docstring).
    shared_ranges = {tech: compute_shared_suitability_range(tech, config) for tech in ("solar", "wind")}

    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        for tech in ("solar", "wind"):
            topsis_path = PROCESSED_DIR / f"topsis_suitability_{region.value}_{tech}.tif"
            mask_path = PROCESSED_DIR / f"exclusion_mask_{region.value}.tif"
            if topsis_path.exists() and mask_path.exists():
                try:
                    results["maps"].append(plot_suitability_map(region, tech, config))
                except Exception as exc:
                    print(f"[plotting] WARNING: suitability map failed for {region.value}/{tech}: {exc}")
            else:
                print(f"[plotting] WARNING: skipping suitability map for {region.value}/{tech} "
                      f"-- {topsis_path.name} or {mask_path.name} not found")

            h2_path = PROCESSED_DIR / f"h2_potential_{region.value}_{tech}_baseline.geojson"
            sites_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.geojson"
            if h2_path.exists() or sites_path.exists():
                try:
                    shared_vmin, shared_vmax = shared_ranges[tech]
                    results["maps"].append(
                        plot_candidate_sites_map(region, tech, config, vmin=shared_vmin, vmax=shared_vmax)
                    )
                except Exception as exc:
                    print(f"[plotting] WARNING: candidate sites map failed for {region.value}/{tech}: {exc}")
            else:
                print(f"[plotting] WARNING: skipping candidate sites map for {region.value}/{tech} "
                      f"-- no candidate_sites/h2_potential geojson found")

    decomposition_csv = TABLES_DIR / "decomposition.csv"
    if decomposition_csv.exists():
        try:
            results["figures"].append(plot_lcoh_decomposition(pd.read_csv(decomposition_csv), config))
        except Exception as exc:
            print(f"[plotting] WARNING: lcoh_decomposition figure failed: {exc}")
    else:
        print(f"[plotting] WARNING: skipping lcoh_decomposition -- {decomposition_csv} not found")

    for renewable_tech in RENEWABLE_TECHS:
        frontier_csv = TABLES_DIR / f"competitiveness_frontier_{renewable_tech}.csv"
        if frontier_csv.exists():
            try:
                results["figures"].append(plot_competitiveness_frontier(config, renewable_tech))
            except Exception as exc:
                print(f"[plotting] WARNING: competitiveness_frontier figure failed for {renewable_tech}: {exc}")
        else:
            print(f"[plotting] WARNING: skipping competitiveness_frontier for {renewable_tech} "
                  f"-- {frontier_csv} not found")

    sens_csv = TABLES_DIR / "economic_sensitivity.csv"
    if sens_csv.exists():
        sens_df = pd.read_csv(sens_csv)
        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            for tech in RENEWABLE_TECHS:
                try:
                    results["figures"].append(plot_sensitivity_tornado(sens_df, region, tech))
                except Exception as exc:
                    print(f"[plotting] WARNING: sensitivity tornado failed for {region.value}/{tech}: {exc}")
    else:
        print(f"[plotting] WARNING: skipping sensitivity tornado charts -- {sens_csv} not found")

    incentive_csv = TABLES_DIR / "incentive_scenarios.csv"
    if incentive_csv.exists():
        try:
            results["figures"].append(plot_incentive_scenarios(pd.read_csv(incentive_csv), config))
        except Exception as exc:
            print(f"[plotting] WARNING: incentive_scenarios figure failed: {exc}")
    else:
        print(f"[plotting] WARNING: skipping incentive_scenarios -- {incentive_csv} not found "
              f"(run economics stage first; incentive_scenarios.py persists this CSV via "
              f"run_all_incentive_scenarios(), see docs/memory/05_economic_model.md)")

    print(
        f"[plotting] generate_all_plots complete in {time.time() - t0:.1f}s: "
        f"{len(results['maps'])} maps, {len(results['figures'])} figures"
    )
    return results
