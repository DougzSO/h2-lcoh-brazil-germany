"""change_list.md item 11: top_n=20 shortlist-size sensitivity check.

Standalone, read-only diagnostic script -- does NOT modify site_selection.py
(top_n is already an existing parameter of select_candidate_sites(), no
code change needed) and does NOT touch any real file under data/processed/:
the two real, already-on-disk spatial-pipeline outputs this needs
(topsis_suitability_brazil_solar.tif, exclusion_mask_brazil.tif) are copied
into an isolated tmp directory, and every downstream module's own
PROCESSED_DIR / H2_POTENTIAL_DIR constant is monkeypatched to that same tmp
directory for the duration of this script -- so re-running
select_candidate_sites()/calculate_h2_potential()/
decompose_actual_per_region_aggregated() at top_n in {10, 20, 30} can never
overwrite the real top_n=20 candidate_sites_brazil_solar.geojson (and
downstream h2_potential_brazil_solar.geojson) the rest of the pipeline's
real outputs depend on.

Rationale for choosing this (Option B) over Option A (collective-share
justification): Option A was computed first and found the top-20 shortlist
captures only 2.1-10.7% of each region/tech's exclusion-mask-suitable area
(see change_list.md's item-11 completion-log entry for the full table) --
far below the 80% threshold that would have made Option A's own
justification sentence usable, because the exclusion mask's "suitable"
total is the land-use/protected-area/boundary filter's raw output BEFORE
site_selection.py's own 85th-percentile pre-filter (which deliberately
discards the bottom 85% of that area before clustering even starts, see
select_candidate_sites()'s own docstring) -- so comparing a 20-site
shortlist against that much larger, mostly-below-threshold universe was
never going to clear 80% by construction. Per this item's own instructions,
falling below 80% on Option A means switching to Option B.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from config.config_loader import load_scenario_config
from src.core.constants import Region
from src.economics import decomposition
from src.potential import h2_potential
from src.spatial import site_selection

REGION = Region.NORDESTE_BR
TECH = "solar"
TOP_N_VALUES = [10, 20, 30]

REAL_PROCESSED_DIR = Path("data/processed")


def main() -> None:
    config = load_scenario_config()

    tmp_dir = Path(tempfile.mkdtemp(prefix="top_n_sensitivity_"))
    try:
        # Seed the sandbox with the two real, already-on-disk spatial
        # outputs this check reads as fixed inputs -- copies only, real
        # files under data/processed/ are never opened for writing.
        shutil.copy(
            REAL_PROCESSED_DIR / f"topsis_suitability_{REGION.value}_{TECH}.tif",
            tmp_dir / f"topsis_suitability_{REGION.value}_{TECH}.tif",
        )
        shutil.copy(
            REAL_PROCESSED_DIR / f"exclusion_mask_{REGION.value}.tif",
            tmp_dir / f"exclusion_mask_{REGION.value}.tif",
        )

        results = []
        with (
            patch.object(site_selection, "PROCESSED_DIR", tmp_dir),
            patch.object(h2_potential, "PROCESSED_DIR", tmp_dir),
            patch.object(decomposition, "H2_POTENTIAL_DIR", tmp_dir),
        ):
            baseline_lcoh_yaml_only = None
            for top_n in TOP_N_VALUES:
                sites = site_selection.select_candidate_sites(REGION, TECH, config, top_n=top_n)
                potential = h2_potential.calculate_h2_potential(REGION, TECH, config)
                aggregated = decomposition.decompose_actual_per_region_aggregated(REGION, TECH, config)

                total_capacity_mw = potential["installable_capacity_mw"].sum()
                total_h2_t = potential["annual_h2_production_t"].sum()
                lcoh_aggregated = aggregated["lcoh_aggregated"]
                baseline_lcoh_yaml_only = aggregated["lcoh_yaml_only"]

                results.append({
                    "top_n": top_n,
                    "n_sites": len(sites),
                    "total_capacity_gw": total_capacity_mw / 1000.0,
                    "total_h2_mt_per_yr": total_h2_t / 1e6,
                    "lcoh_usd_per_kg": lcoh_aggregated,
                })

        baseline_row = next(r for r in results if r["top_n"] == 20)
        baseline_lcoh = baseline_row["lcoh_usd_per_kg"]

        print()
        print(f"{'Region/Tech':<15}{'top_n':>7}{'N sites':>9}{'Total H2 (Mt/yr)':>19}{'LCOH (USD/kg)':>16}{'Delta from baseline':>22}")
        for r in results:
            delta_pct = 100.0 * (r["lcoh_usd_per_kg"] - baseline_lcoh) / baseline_lcoh
            label = "baseline" if r["top_n"] == 20 else f"{delta_pct:+.3f}%"
            print(
                f"{REGION.value + '/' + TECH:<15}{r['top_n']:>7}{r['n_sites']:>9}"
                f"{r['total_h2_mt_per_yr']:>19.4f}{r['lcoh_usd_per_kg']:>16.4f}{label:>22}"
            )

        min_lcoh = min(r["lcoh_usd_per_kg"] for r in results)
        max_lcoh = max(r["lcoh_usd_per_kg"] for r in results)
        variance_pct = 100.0 * (max_lcoh - min_lcoh) / baseline_lcoh
        print()
        print(f"LCOH range across top_n in {{10, 20, 30}}: {min_lcoh:.4f}-{max_lcoh:.4f} USD/kg")
        print(f"Variance relative to top_n=20 baseline ({baseline_lcoh:.4f} USD/kg): {variance_pct:.3f}%")
        print(f"lcoh_yaml_only (config-only, not site-dependent, for reference): {baseline_lcoh_yaml_only:.4f} USD/kg")
        print(f"Result: {'INSENSITIVE (<5%)' if variance_pct < 5.0 else 'MATERIAL (>=5%) -- flag as HIGH priority'}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
