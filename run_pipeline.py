"""
run_pipeline.py
================
Orchestrates the full hydrogen LCOH decomposition pipeline, end to end.

Design principle (unchanged from the original scaffold): this script
contains no analytical logic of its own. Every stage function is a thin
wrapper that calls already-implemented, already-verified public functions
from src/ and reports their outcome -- it never reimplements TOPSIS/VIKOR
math, the LCOH formula, or any other analytical model.

Stage execution is always in this fixed dependency order, regardless of
the order --stage flags are passed:

    1. config       -- load and validate ScenarioConfig (always run first)
    2. acquisition  -- fetch() every raw data source, per region
    3. spatial      -- admin_boundaries -> data_layers -> exclusion_mask
                        -> topsis & vikor -> site_selection
    4. potential    -- calculate_h2_potential() per candidate-site region/tech
    5. economics    -- run_all_decompositions() + run_all_incentive_scenarios()
    6. sensitivity  -- run_sensitivity() (economic OAT) + run_sobol_analysis()
                        (Sobol global variance-based sensitivity) +
                        run_mcda_sensitivity() (TOPSIS weight-perturbation
                        vs VIKOR concordance)
    7. viz          -- generate_all_plots()

ROBUSTNESS: every stage that depends on upstream files this environment
cannot produce without live network access (acquisition, spatial,
potential, and sensitivity's MCDA half) catches per-region/per-technology
failures individually and records them as "skipped"/"failed" in the run
summary, rather than letting one missing input abort the whole run.
economics, sensitivity's economic and Sobol halves, and viz never depend on
live acquisition data (pure config-driven math, or graceful-skip-on-missing-CSV
by design in viz/plotting.py) and are expected to always succeed.

Usage
-----
    python run_pipeline.py --stage economics --stage sensitivity --stage viz
    python run_pipeline.py --stage all --region brazil
    python run_pipeline.py --stage all --skip-acquisition
    python run_pipeline.py --stage spatial --region germany
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

BOUNDARIES_DIR = Path("data/boundaries")
TABLES_DIR = Path("outputs/tables")

STAGE_ORDER = ["acquisition", "spatial", "potential", "economics", "sensitivity", "viz"]
STAGE_CHOICES = STAGE_ORDER + ["all"]
REGION_CHOICES = ["brazil", "germany", "all"]

# site_selection.select_candidate_sites()'s top_suitability_percentile +
# min/max_contiguous_suitable_area_km2 thresholds (config-driven) already
# rule out both bioma-scale blobs and sub-hub-scale fragments, but a fairly
# uniform TOPSIS raster over a large region can still leave hundreds of
# individually-valid disjoint patches. METHODOLOGY.md 2.3: "the top-ranked
# units were retained as candidate hydrogen hubs" -- a shortlist, not every
# passing patch -- so the pipeline caps the final ranked list here.
SITE_SELECTION_TOP_N = 20


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _count_statuses(node) -> dict:
    """Recursively tally every {"status": "..."} leaf found anywhere in a
    nested stage-result dict, for a compact one-line progress recap."""
    counts: dict = {}
    if isinstance(node, dict):
        if "status" in node and isinstance(node["status"], str):
            counts[node["status"]] = counts.get(node["status"], 0) + 1
            return counts
        for value in node.values():
            for key, count in _count_statuses(value).items():
                counts[key] = counts.get(key, 0) + count
    return counts


def _print_config_summary(config) -> None:
    """
    Prints one sub-table PER COMBINED SCENARIO (min/baseline/max) -- the
    pipeline no longer selects a single scenario.active branch, it always
    runs and reports all three (see scenario_params.yaml's top-of-file
    comment). Always 3 separate sub-tables rather than one wide table,
    per the task's own ">100 chars wide -> separate sub-tables" fallback
    (simpler and always correct regardless of terminal width). Columns:
    Region, Tech, WACC, CF, FLH, Ren.CAPEX, Cap.Density -- all resolved at
    that sub-table's own scenario branch via decomposition.py's
    scenario-aware resolvers (_tech_wacc, _effective_renewable_capex_for_scenario)
    and config_loader.resolve_param(). Scalar parameters that do NOT vary
    by scenario (electrolyzer CAPEX/efficiency, OPEX%) print once in a
    separate summary block below the three sub-tables.
    """
    from config.config_loader import resolve_param
    from src.economics.decomposition import (
        _effective_renewable_capex_for_scenario,
        _region_config,
        _tech_wacc,
    )
    from src.core.constants import Region

    region_map = (("brazil", "Brazil", Region.NORDESTE_BR), ("germany", "Germany", Region.NORTH_GERMANY))
    tech_map = (("solar_pv", "Solar"), ("onshore_wind", "Wind"))

    for scenario in ("min", "baseline", "max"):
        print(f"\n[run_pipeline] combined scenario = '{scenario}'")
        print(f"{'Region':<8}{'Tech':<7}{'WACC':>7}  {'Cap.Factor':>10}  {'FLH':>6}  {'Ren.CAPEX':>9}  {'Cap.Density':>11}")
        for region_key, region_label, region_enum in region_map:
            region_cfg = _region_config(region_enum, config)
            for tech_key, tech_label in tech_map:
                p = getattr(getattr(config.technologies, tech_key), region_key)
                renewable_cfg = getattr(config.renewables, tech_key)
                wacc = _tech_wacc(region_cfg, tech_key, scenario=scenario)
                cf = resolve_param(p.capacity_factor, scenario)
                flh = resolve_param(p.full_load_hours, scenario)
                dens = resolve_param(p.capacity_density_mw_per_km2, scenario)
                ren_capex = _effective_renewable_capex_for_scenario(
                    region_cfg, renewable_cfg, tech_key, scenario
                )
                print(
                    f"{region_label:<8}{tech_label:<7}{wacc * 100:>6.1f}%  "
                    f"{cf * 100:>9.1f}%  {flh:>6.0f}  {ren_capex:>9.0f}  {dens:>9.1f}"
                )

    print("\n[run_pipeline] scenario-invariant scalars (fixed across all 3 scenarios)")
    for tech_name, tech_cfg in config.electrolyzer.technologies.items():
        print(
            f"  electrolyzer/{tech_name:<8} CAPEX={tech_cfg.capex_usd_per_kw:>7.0f} USD/kW  "
            f"efficiency={tech_cfg.efficiency_kwh_per_kg:>5.1f} kWh/kg"
        )
    print(f"  electrolyzer/shared    OPEX={config.electrolyzer.shared.opex_pct_of_capex * 100:.1f}% of CAPEX/yr")
    for tech_key, tech_label in tech_map:
        r = getattr(config.renewables, tech_key)
        print(f"  renewables/{tech_label:<7}     OPEX={r.opex_pct_of_capex * 100:.1f}% of CAPEX/yr, lifetime={r.lifetime_years}yr")

    print("\n[run_pipeline] Ctrl+C within 2s to abort if parameters above look wrong.")
    try:
        time.sleep(2)
    except KeyboardInterrupt:
        sys.exit("\n[run_pipeline] Aborted during config review.")


# ---------------------------------------------------------------------------
# Stage 1: config
# ---------------------------------------------------------------------------

def stage_config():
    """Load and validate scenario_params.yaml. Fails loudly on any
    schema/cross-field validation error (see config_loader.py)."""
    from config.config_loader import load_scenario_config

    logger.info("Loading and validating scenario_params.yaml ...")
    config = load_scenario_config()
    logger.info("Config OK. Regions: %s", list(type(config.regions).model_fields.keys()))
    return config


# ---------------------------------------------------------------------------
# Stage 2: acquisition
# ---------------------------------------------------------------------------

def stage_acquisition(regions, config) -> dict:
    """Run every acquisition module's fetch(region, config) for the
    requested regions. Each fetch is wrapped individually so one
    unreachable/uncredentialed source does not abort the rest -- every
    fetch() function still fails explicitly with its own descriptive
    error (Hard Rule 6, root CLAUDE.md); this only prevents that explicit
    failure from crashing the whole multi-source, multi-region run."""
    from src.acquisition import (
        admin_boundaries_fetch, grid_infrastructure_fetch, landuse_fetch,
        protected_areas_fetch, solar_wind_atlas, srtm, water_bodies_fetch,
    )

    fetchers = [
        ("admin_boundaries", admin_boundaries_fetch.fetch),
        ("srtm", srtm.fetch),
        ("solar_wind", solar_wind_atlas.fetch),
        ("water_bodies", water_bodies_fetch.fetch),
        ("grid_infrastructure", grid_infrastructure_fetch.fetch),
        ("landuse", landuse_fetch.fetch),
        ("protected_areas", protected_areas_fetch.fetch),
    ]

    results = {}
    for region in regions:
        region_result = {}
        region_t0 = time.time()
        n_ok = 0
        for name, fetch_fn in fetchers:
            t0 = time.time()
            try:
                output = fetch_fn(region, config)
                region_result[name] = {
                    "status": "ok", "output": str(output),
                    "elapsed_s": round(time.time() - t0, 1),
                }
                n_ok += 1
            except Exception as exc:
                region_result[name] = {"status": "failed", "error": str(exc)}
                logger.warning("  acquisition/%s (%s): FAILED -- %s", name, region.value, exc)
        results[region.value] = region_result
        # Single consolidated summary line per region -- replaces the
        # previous per-fetcher "OK in Xs" line (7 lines/region); per-fetcher
        # FAILED warnings above are untouched, so a real failure is never
        # hidden by this consolidation.
        logger.info(
            "acquisition (%s): %d/%d layers OK in %.1fs",
            region.value, n_ok, len(fetchers), time.time() - region_t0,
        )
    return results


# ---------------------------------------------------------------------------
# Stage 3: spatial
# ---------------------------------------------------------------------------

def stage_spatial(regions, config) -> dict:
    """admin_boundaries -> data_layers -> exclusion_mask -> topsis & vikor
    -> site_selection, per region (and per siting technology for the last
    three). Each step is wrapped so a missing upstream input -- expected
    whenever live acquisition hasn't populated data/raw/ -- is recorded as
    a skip rather than an unhandled crash that aborts sibling regions.

    NOT looped over the three combined scenarios (min/baseline/max): this
    entire stage consumes only raw GHI/wind-power-density/slope/distance
    rasters and land-use/protected-area/boundary layers -- it never reads
    capacity_factor, capacity_density, or WACC, so it is scenario-invariant
    by construction. Running it three times would produce three
    byte-identical outputs. Only stage_potential() (capacity_factor +
    capacity_density) and stage_economics() (WACC + capacity_factor +
    renewable CAPEX) actually vary across scenarios."""
    import csv

    from src.spatial import data_layers
    from src.spatial.admin_boundaries import AdminBoundaryError, get_dissolved_polygon
    from src.spatial.exclusion_mask import create_exclusion_mask
    from src.spatial.site_selection import SiteSelectionError, select_candidate_sites
    from src.spatial.topsis import TopsisError, compute_resource_suitability_correlation, run_topsis
    from src.spatial.vikor import VikorError, run_vikor

    adaptive_check_rows = []

    data_loaders = [
        ("solar_irradiance", data_layers.load_solar_irradiance),
        ("wind_power_density", data_layers.load_wind_power_density),
        ("slope", data_layers.load_slope),
        ("distance_to_water", data_layers.load_distance_to_water),
        ("distance_to_grid", data_layers.load_distance_to_grid),
    ]

    results = {}
    for region in regions:
        region_result: dict = {}

        try:
            boundary = get_dissolved_polygon(region)
            BOUNDARIES_DIR.mkdir(parents=True, exist_ok=True)
            boundary_path = BOUNDARIES_DIR / f"{region.value}.geojson"
            if not boundary_path.exists():
                boundary.to_file(boundary_path, driver="GeoJSON")
            region_result["admin_boundaries"] = {"status": "ok"}
        except AdminBoundaryError as exc:
            region_result["admin_boundaries"] = {"status": "failed", "error": str(exc)}
            results[region.value] = region_result
            continue  # nothing downstream can proceed without a boundary

        data_layers_status = {}
        for name, loader_fn in data_loaders:
            try:
                loader_fn(region, config)
                data_layers_status[name] = {"status": "ok"}
            except FileNotFoundError as exc:
                data_layers_status[name] = {"status": "skipped", "error": str(exc)}
        region_result["data_layers"] = data_layers_status

        try:
            create_exclusion_mask(region, config)
            region_result["exclusion_mask"] = {"status": "ok"}
        except FileNotFoundError as exc:
            region_result["exclusion_mask"] = {"status": "skipped", "error": str(exc)}
            results[region.value] = region_result
            continue  # topsis/vikor/site_selection all require the mask

        for tech in ("solar", "wind"):
            tech_result = {}
            vector_scores = None
            try:
                vector_scores, _ = run_topsis(region, tech, config)
                tech_result["topsis"] = {"status": "ok"}
            except (FileNotFoundError, TopsisError) as exc:
                tech_result["topsis"] = {"status": "skipped", "error": str(exc)}

            # Adaptive-normalization robustness check: side-by-side
            # comparison only, logged + written to outputs/tables/, never
            # feeding into site_selection/h2_potential (see
            # docs/memory/04_spatial_methodology.md's "Adaptive
            # normalization" section for why vector stays primary).
            norm_cfg = getattr(getattr(config, "topsis", None), "normalization", None)
            if vector_scores is not None and getattr(norm_cfg, "run_adaptive_robustness_check", False):
                try:
                    vector_corr = compute_resource_suitability_correlation(region, tech, config, vector_scores)
                    adaptive_scores, _ = run_topsis(region, tech, config, norm_method="adaptive")
                    adaptive_corr = compute_resource_suitability_correlation(region, tech, config, adaptive_scores)
                    for method_name, corr in (("vector", vector_corr), ("adaptive", adaptive_corr)):
                        adaptive_check_rows.append({
                            "region": region.value, "tech": tech, "norm_method": method_name,
                            "resource_suitability_pearson_r": round(corr["pearson_r"], 4),
                            "resource_suitability_spearman_r": round(corr["spearman_r"], 4),
                        })
                    logger.info(
                        "  [adaptive-check] %s/%s: resource-suitability r vector=%.4f adaptive=%.4f",
                        region.value, tech, vector_corr["pearson_r"], adaptive_corr["pearson_r"],
                    )
                    tech_result["adaptive_check"] = {"status": "ok"}
                except (FileNotFoundError, TopsisError) as exc:
                    tech_result["adaptive_check"] = {"status": "skipped", "error": str(exc)}
            try:
                run_vikor(region, tech, config)
                tech_result["vikor"] = {"status": "ok"}
            except (FileNotFoundError, VikorError) as exc:
                tech_result["vikor"] = {"status": "skipped", "error": str(exc)}
            try:
                gdf = select_candidate_sites(region, tech, config, top_n=SITE_SELECTION_TOP_N)
                tech_result["site_selection"] = {"status": "ok", "sites": len(gdf)}
            except (FileNotFoundError, SiteSelectionError) as exc:
                tech_result["site_selection"] = {"status": "skipped", "error": str(exc)}
            region_result[tech] = tech_result

        results[region.value] = region_result

    if adaptive_check_rows:
        out_path = TABLES_DIR / "topsis_adaptive_robustness_check.csv"
        TABLES_DIR.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(adaptive_check_rows[0].keys()))
            writer.writeheader()
            writer.writerows(adaptive_check_rows)
        logger.info("  [adaptive-check] wrote %s (%d rows)", out_path, len(adaptive_check_rows))

    return results


# ---------------------------------------------------------------------------
# Stage 4: potential
# ---------------------------------------------------------------------------

def stage_potential(regions, config) -> dict:
    """calculate_h2_potential() per region/siting-technology PER COMBINED
    SCENARIO (min/baseline/max) -- capacity_factor and capacity_density
    genuinely vary by scenario (unlike site_selection.py's candidate sites,
    which are scenario-invariant -- see stage_spatial()'s own docstring),
    so this is the one spatial-adjacent stage that actually needs the 3x
    loop. Writes h2_potential_{region}_{tech}_{scenario}.geojson/.csv.
    Missing input (no candidate sites yet) is a per-pair-per-scenario skip,
    not a hard failure."""
    from src.potential.h2_potential import calculate_h2_potential

    results = {}
    for region in regions:
        region_result = {}
        for tech in ("solar", "wind"):
            tech_result = {}
            for scenario in ("min", "baseline", "max"):
                try:
                    gdf = calculate_h2_potential(region, tech, config, scenario=scenario)
                    tech_result[scenario] = {"status": "ok", "sites": len(gdf)}
                except FileNotFoundError as exc:
                    tech_result[scenario] = {"status": "skipped", "error": str(exc)}
            region_result[tech] = tech_result
        results[region.value] = region_result
    return results


# ---------------------------------------------------------------------------
# Stage 5: economics
# ---------------------------------------------------------------------------

def stage_economics(config) -> dict:
    """run_all_decompositions() (actual / WACC-swap / combined-scenario
    LCOH range, writes decomposition.csv), run_all_incentive_scenarios()
    (REHIDRO/IPCEI Hy2Use, writes incentive_scenarios.csv), and
    competitiveness_frontier.run_all_frontiers() (2D WACC_BR x WACC_DE
    frontier, writes competitiveness_frontier_{tech}.csv) -- run once,
    after run_all_decompositions()'s three combined scenarios have already
    completed, using baseline parameters for every non-WACC variable (see
    ADR-009, docs/memory/06_technical_decisions_log.md). Pure config-driven
    math -- no raw acquired data required, so this stage is expected to
    always succeed."""
    from src.economics.decomposition import run_all_decompositions
    from src.economics.incentive_scenarios import run_all_incentive_scenarios
    from src.economics.competitiveness_frontier import run_all_frontiers

    run_all_decompositions(config)
    incentive_results = run_all_incentive_scenarios(config)
    frontier_results = run_all_frontiers(config)
    return {
        "status": "ok",
        "incentive_scenarios": incentive_results,
        "competitiveness_frontier": {
            tech: r["summary"] for tech, r in frontier_results.items()
        },
    }


# ---------------------------------------------------------------------------
# Stage 6: sensitivity
# ---------------------------------------------------------------------------

def stage_sensitivity(regions, config) -> dict:
    """run_sensitivity() (economic OAT sweep, pure config-driven math,
    always succeeds) plus run_sobol_analysis() (Sobol global sensitivity,
    also pure config-driven math, always succeeds) plus
    run_mcda_sensitivity() per region/siting-tech (TOPSIS
    weight-perturbation vs VIKOR concordance, requires the same spatial
    criterion rasters as stage_spatial -- each pair is wrapped individually
    so a missing raster skips only that pair)."""
    from src.sensitivity.sensitivity_analysis import (
        run_mcda_sensitivity,
        run_sensitivity,
        run_sobol_analysis,
    )

    economic_df = run_sensitivity(config)
    sobol_results = run_sobol_analysis(config)
    sobol_summary = {key: {"status": "ok", "rows": len(df)} for key, df in sobol_results.items()}
    mcda_results = {}
    for region in regions:
        for tech in ("solar", "wind"):
            key = f"{region.value}_{tech}"
            try:
                mcda_df = run_mcda_sensitivity(region, tech, config)
                mcda_results[key] = {"status": "ok", "rows": len(mcda_df)}
            except Exception as exc:
                mcda_results[key] = {"status": "skipped", "error": str(exc)}
    return {
        "economic": {"status": "ok", "rows": len(economic_df)},
        "sobol": sobol_summary,
        "mcda": mcda_results,
    }


# ---------------------------------------------------------------------------
# Stage 7: viz
# ---------------------------------------------------------------------------

def stage_viz(config) -> dict:
    """generate_all_plots() -- already gracefully skips (with its own
    warning prints) any map/figure whose prerequisite file doesn't exist
    yet, so no extra per-plot error handling is needed here."""
    from src.viz.plotting import generate_all_plots

    results = generate_all_plots(config)
    return {"status": "ok", "maps": len(results["maps"]), "figures": len(results["figures"])}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hydrogen LCOH decomposition pipeline orchestrator."
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGE_CHOICES,
        default=None,
        help="Stage(s) to run. Repeatable (e.g. --stage economics --stage "
             "viz). 'all' runs every stage in fixed dependency order "
             "regardless of flag order. Default: all.",
    )
    parser.add_argument(
        "--region",
        choices=REGION_CHOICES,
        default="all",
        help="Region to run stages for: 'brazil', 'germany', or 'all' (default).",
    )
    parser.add_argument(
        "--skip-acquisition",
        action="store_true",
        help="Skip the acquisition stage even if requested, running from "
             "already-downloaded/cached files in data/raw/ and "
             "data/boundaries/ without making any network call.",
    )
    args = parser.parse_args()

    from src.core.constants import Region

    region_map = {"brazil": Region.NORDESTE_BR, "germany": Region.NORTH_GERMANY}
    regions = list(region_map.values()) if args.region == "all" else [region_map[args.region]]

    requested = set(args.stage or ["all"])
    stages_to_run = STAGE_ORDER if "all" in requested else [s for s in STAGE_ORDER if s in requested]

    t_pipeline = time.time()
    summary: dict = {}

    _banner("STAGE: config")
    t0 = time.time()
    config = stage_config()
    print(f"[run_pipeline] config loaded in {time.time() - t0:.1f}s")
    _print_config_summary(config)

    if "acquisition" in stages_to_run:
        _banner("STAGE: acquisition")
        t0 = time.time()
        if args.skip_acquisition:
            logger.info("Skipping acquisition (--skip-acquisition) -- using cached data/raw/, data/boundaries/")
            summary["acquisition"] = {"status": "skipped (--skip-acquisition)"}
        else:
            try:
                summary["acquisition"] = stage_acquisition(regions, config)
            except Exception as exc:
                logger.error("acquisition stage failed unexpectedly: %s", exc)
                summary["acquisition"] = {"status": "fatal_error", "error": str(exc)}
        print(
            f"[run_pipeline] acquisition stage finished in {time.time() - t0:.1f}s "
            f"-- {_count_statuses(summary['acquisition'])}"
        )

    if "spatial" in stages_to_run:
        _banner("STAGE: spatial")
        t0 = time.time()
        try:
            summary["spatial"] = stage_spatial(regions, config)
        except Exception as exc:
            logger.error("spatial stage failed unexpectedly: %s", exc)
            summary["spatial"] = {"status": "fatal_error", "error": str(exc)}
        print(
            f"[run_pipeline] spatial stage finished in {time.time() - t0:.1f}s "
            f"-- {_count_statuses(summary['spatial'])}"
        )

    if "potential" in stages_to_run:
        _banner("STAGE: potential")
        t0 = time.time()
        try:
            summary["potential"] = stage_potential(regions, config)
        except Exception as exc:
            logger.error("potential stage failed unexpectedly: %s", exc)
            summary["potential"] = {"status": "fatal_error", "error": str(exc)}
        print(
            f"[run_pipeline] potential stage finished in {time.time() - t0:.1f}s "
            f"-- {_count_statuses(summary['potential'])}"
        )

    if "economics" in stages_to_run:
        _banner("STAGE: economics")
        t0 = time.time()
        try:
            summary["economics"] = stage_economics(config)
        except Exception as exc:
            logger.error("economics stage failed unexpectedly: %s", exc)
            summary["economics"] = {"status": "fatal_error", "error": str(exc)}
        print(f"[run_pipeline] economics stage finished in {time.time() - t0:.1f}s")

    if "sensitivity" in stages_to_run:
        _banner("STAGE: sensitivity")
        t0 = time.time()
        try:
            summary["sensitivity"] = stage_sensitivity(regions, config)
        except Exception as exc:
            logger.error("sensitivity stage failed unexpectedly: %s", exc)
            summary["sensitivity"] = {"status": "fatal_error", "error": str(exc)}
        print(
            f"[run_pipeline] sensitivity stage finished in {time.time() - t0:.1f}s "
            f"-- {_count_statuses(summary['sensitivity'])}"
        )

    if "viz" in stages_to_run:
        _banner("STAGE: viz")
        t0 = time.time()
        try:
            summary["viz"] = stage_viz(config)
        except Exception as exc:
            logger.error("viz stage failed unexpectedly: %s", exc)
            summary["viz"] = {"status": "fatal_error", "error": str(exc)}
        print(f"[run_pipeline] viz stage finished in {time.time() - t0:.1f}s")

    _banner("PIPELINE RUN SUMMARY")
    print(json.dumps(summary, indent=2, default=str))
    print(f"\n[run_pipeline] total elapsed: {time.time() - t_pipeline:.1f}s")


if __name__ == "__main__":
    main()
