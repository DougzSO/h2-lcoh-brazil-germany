"""
src/economics/competitiveness_frontier.py

2D WACC_BR x WACC_DE competitiveness frontier, replacing the old 1D
Brazil-only inversion-point search that used to live in decomposition.py
(see ADR-009, docs/memory/06_technical_decisions_log.md). Deterministic
grid search per renewable technology -- no Monte Carlo -- with every
non-WACC parameter (capacity factor, capacity density, renewable CAPEX,
OPEX, lifetime, electrolyzer) pinned at its own `scenario="baseline"`
branch via decomposition._lcoh_for(), reused as-is: no LCOH formula is
duplicated here.

Two policy-relevant subsidy-equivalence metrics are computed per
technology:
  1. WACC-gap closure: fixing Germany's WACC at its own baseline, the
     Brazilian WACC at which LCOH_BR == LCOH_DE (root-found via
     scipy.optimize.brentq along that one WACC_DE=baseline slice of the
     grid -- the same root-finding mechanism the old 1D search used,
     scoped only inside this module rather than reintroduced as a
     standalone 1D config concept).
  2. Direct support: LCOH_BR - LCOH_DE, the flat per-kg production credit
     that would achieve parity without any WACC change. Computed at all
     THREE combined scenarios (min/baseline/max -- see
     direct_support_by_scenario()), not just baseline, and reported as a
     range (ADR-013, docs/memory/06_technical_decisions_log.md): each
     combined scenario moves BOTH regions to the SAME branch
     simultaneously (worst-case-for-both, baseline, best-case-for-both --
     never one region at "min" while the other is at "max"), so the range
     reflects genuinely different joint financing/resource states, not an
     artificial spread. Neither the range's sign nor which endpoint is
     larger is forced anywhere in this module -- both are read off
     whatever the three scenario computations actually produce.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from src.core.constants import Region
from src.economics.decomposition import _lcoh_for, _region_config, _tech_wacc

OUTPUT_DIR = "outputs/tables"

_TECH_WACC_KEY: Dict[str, str] = {"solar_pv": "solar", "onshore_wind": "wind"}
_COMBINED_SCENARIOS = ("min", "baseline", "max")


def _frontier_csv_path(renewable_tech: str) -> str:
    return os.path.join(OUTPUT_DIR, f"competitiveness_frontier_{renewable_tech}.csv")


def compute_frontier(config, renewable_tech: str) -> pd.DataFrame:
    """
    Build the deterministic WACC_BR x WACC_DE grid for one renewable
    technology and compute LCOH_BR/LCOH_DE at every grid point, all other
    parameters pinned at baseline (scenario="baseline", the default).

    Grid: config.competitiveness_frontier.wacc_steps points per axis
    (15-20), spanning each region's own full wacc.<tech> {min, max} range
    for this technology (RangeParam bounds -- NOT the combined-scenario
    inversion _tech_wacc() applies elsewhere; here WACC is swept directly
    via wacc_override, bypassing the combined-scenario resolution entirely).

    Returns
    -------
    pd.DataFrame
        Columns: technology, wacc_br, wacc_de, lcoh_br, lcoh_de,
        delta_lcoh, parity_flag. Written to
        outputs/tables/competitiveness_frontier_{renewable_tech}.csv.
    """
    if renewable_tech not in _TECH_WACC_KEY:
        raise ValueError(f"renewable_tech must be 'solar_pv' or 'onshore_wind', got {renewable_tech!r}")
    tech_key = _TECH_WACC_KEY[renewable_tech]

    br_cfg = _region_config(Region.NORDESTE_BR, config)
    de_cfg = _region_config(Region.NORTH_GERMANY, config)
    br_range = getattr(br_cfg.wacc, tech_key)
    de_range = getattr(de_cfg.wacc, tech_key)

    steps = config.competitiveness_frontier.wacc_steps
    tolerance_pct = config.competitiveness_frontier.parity_tolerance_pct

    wacc_br_values = np.linspace(br_range.min, br_range.max, steps)
    wacc_de_values = np.linspace(de_range.min, de_range.max, steps)

    rows = []
    for wacc_br in wacc_br_values:
        for wacc_de in wacc_de_values:
            lcoh_br = _lcoh_for(
                Region.NORDESTE_BR, config, renewable_tech=renewable_tech,
                wacc_override=float(wacc_br), scenario="baseline",
            )
            lcoh_de = _lcoh_for(
                Region.NORTH_GERMANY, config, renewable_tech=renewable_tech,
                wacc_override=float(wacc_de), scenario="baseline",
            )
            delta_lcoh = lcoh_br - lcoh_de
            mean_lcoh = (lcoh_br + lcoh_de) / 2.0
            parity_flag = mean_lcoh > 0 and abs(delta_lcoh) <= tolerance_pct * mean_lcoh
            rows.append({
                "technology": renewable_tech,
                "wacc_br": float(wacc_br),
                "wacc_de": float(wacc_de),
                "lcoh_br": lcoh_br,
                "lcoh_de": lcoh_de,
                "delta_lcoh": delta_lcoh,
                "parity_flag": parity_flag,
            })

    df = pd.DataFrame(rows)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = _frontier_csv_path(renewable_tech)
    df.to_csv(out_path, index=False)
    print(f"[competitiveness_frontier] wrote {out_path} ({len(df)} grid points, {steps}x{steps})")
    return df


def direct_support_by_scenario(config, renewable_tech: str) -> Dict[str, float]:
    """
    direct_support = LCOH_BR - LCOH_DE at each of the three COMBINED
    scenario branches (min/baseline/max), both regions resolved at the
    SAME branch simultaneously via decomposition._lcoh_for()'s own
    `scenario` parameter (no `wacc_override` -- WACC, capacity factor, and
    renewable CAPEX all move together, per the combined-scenario design;
    see docs/memory/09_methodology_assumptions.md's "Combined scenarios"
    section). This is a different sweep from compute_frontier()'s
    independent WACC_BR x WACC_DE grid: here Brazil and Germany are never
    at two different combined branches in the same computation.

    Positive = Brazil needs a production credit to reach parity at that
    scenario; negative = Brazil already has a natural cost advantage.
    Neither sign nor magnitude is forced -- each of the three values is
    whatever _lcoh_for() actually returns for that branch.
    """
    if renewable_tech not in _TECH_WACC_KEY:
        raise ValueError(f"renewable_tech must be 'solar_pv' or 'onshore_wind', got {renewable_tech!r}")
    return {
        scenario: (
            _lcoh_for(Region.NORDESTE_BR, config, renewable_tech=renewable_tech, scenario=scenario)
            - _lcoh_for(Region.NORTH_GERMANY, config, renewable_tech=renewable_tech, scenario=scenario)
        )
        for scenario in _COMBINED_SCENARIOS
    }


def _interpret_direct_support_range(range_min: float, range_max: float) -> str:
    """Natural-language interpretation of a direct-support range, per the
    three cases below -- whichever case applies is read off range_min/
    range_max as computed, never chosen or forced in advance."""
    if range_max <= 0.0:
        # Brazil has an advantage in every scenario tested.
        low_advantage, high_advantage = -range_max, -range_min
        if abs(high_advantage - low_advantage) < 1e-9:
            return (
                f"Brazil has a ${high_advantage:.2f}/kg natural cost advantage "
                f"in every scenario; no production incentive needed."
            )
        return (
            f"Brazil has a natural cost advantage in every scenario, ranging "
            f"from ${low_advantage:.2f}/kg to ${high_advantage:.2f}/kg; no "
            f"production incentive needed."
        )
    if range_min >= 0.0:
        # Brazil needs an incentive in every scenario tested.
        if abs(range_max - range_min) < 1e-9:
            return (
                f"Brazil needs a ${range_max:.2f}/kg production incentive to "
                f"reach parity in every scenario."
            )
        return (
            f"Brazil needs a production incentive in every scenario, ranging "
            f"from ${range_min:.2f}/kg to ${range_max:.2f}/kg to reach parity."
        )
    # The range straddles zero: some scenarios need support, others don't.
    return (
        f"In the worst case Brazil needs ${range_max:.2f}/kg; in the best "
        f"case it has ${-range_min:.2f}/kg advantage."
    )


def direct_support_range(config, renewable_tech: str) -> dict:
    """
    Public entry point for the scenario-range direct-support metric:
    computes direct_support_by_scenario(), takes the actual min/max across
    the three values (not a fixed min="min scenario"/max="max scenario"
    assignment -- both regions moving together means the "min" combined
    scenario is not guaranteed to produce the largest gap), and prints the
    exact requested terminal line plus a plain-language interpretation.

    Reused by both summarize_frontier() (below) and
    incentive_scenarios.py's incentive-equivalence analysis, so the two
    modules can never silently diverge onto two different direct-support
    numbers for the same technology.
    """
    by_scenario = direct_support_by_scenario(config, renewable_tech)
    range_min = min(by_scenario.values())
    range_max = max(by_scenario.values())
    baseline = by_scenario["baseline"]
    interpretation = _interpret_direct_support_range(range_min, range_max)

    print(
        f"[frontier] {renewable_tech} direct support range: "
        f"${range_min:.2f} - ${range_max:.2f}/kg (baseline ${baseline:.2f})"
    )
    print(f"[frontier] {renewable_tech}: {interpretation}")

    return {
        "technology": renewable_tech,
        "by_scenario_usd_per_kg": by_scenario,
        "range_min_usd_per_kg": range_min,
        "range_max_usd_per_kg": range_max,
        "baseline_usd_per_kg": baseline,
        "interpretation": interpretation,
    }


def summarize_frontier(config, renewable_tech: str, frontier_df: pd.DataFrame) -> dict:
    """
    Compute and print both subsidy-equivalence metrics for one renewable
    technology, from a frontier grid already computed by compute_frontier().

    Metric 1 (WACC-gap closure): holds Germany's WACC at its own baseline
    and root-finds (brentq) the Brazilian WACC at which LCOH_BR == LCOH_DE
    (baseline), bounded by Brazil's own wacc.<tech> {min, max} range --
    same root-finding mechanism the old 1D inversion search used, scoped
    only inside this function. If no sign change exists across that range,
    reports "no frontier found" with the direction (always cheaper/always
    more expensive) -- a valid result, not suppressed.

    Metric 2 (Direct support): LCOH_BR(baseline) - LCOH_DE(baseline)
    directly, no root-finding needed.
    """
    if renewable_tech not in _TECH_WACC_KEY:
        raise ValueError(f"renewable_tech must be 'solar_pv' or 'onshore_wind', got {renewable_tech!r}")
    tech_key = _TECH_WACC_KEY[renewable_tech]

    br_cfg = _region_config(Region.NORDESTE_BR, config)
    de_cfg = _region_config(Region.NORTH_GERMANY, config)
    br_range = getattr(br_cfg.wacc, tech_key)

    baseline_br_wacc = _tech_wacc(br_cfg, renewable_tech, scenario="baseline")
    baseline_de_wacc = _tech_wacc(de_cfg, renewable_tech, scenario="baseline")

    lcoh_br_baseline = _lcoh_for(
        Region.NORDESTE_BR, config, renewable_tech=renewable_tech,
        wacc_override=baseline_br_wacc, scenario="baseline",
    )
    lcoh_de_baseline = _lcoh_for(
        Region.NORTH_GERMANY, config, renewable_tech=renewable_tech,
        wacc_override=baseline_de_wacc, scenario="baseline",
    )

    # Metric 2: direct support -- no root-find needed. Computed at all
    # three combined scenarios (min/baseline/max) via direct_support_range()
    # below; its "baseline" component is bit-for-bit the same computation
    # as lcoh_br_baseline - lcoh_de_baseline above (same scenario="baseline"
    # resolution path, no wacc_override needed since _tech_wacc(scenario=
    # "baseline") returns the same value either way).
    direct_support_result = direct_support_range(config, renewable_tech)
    direct_support = direct_support_result["baseline_usd_per_kg"]

    # Metric 1: WACC-gap closure, root-found along the wacc_de=baseline slice.
    def _gap(candidate_wacc_br: float) -> float:
        lcoh_br = _lcoh_for(
            Region.NORDESTE_BR, config, renewable_tech=renewable_tech,
            wacc_override=candidate_wacc_br, scenario="baseline",
        )
        return lcoh_br - lcoh_de_baseline

    gap_low = _gap(br_range.min)
    gap_high = _gap(br_range.max)

    parity_br_wacc: Optional[float] = None
    closure_value: Optional[float] = None
    no_frontier_direction: Optional[str] = None

    if gap_low * gap_high > 0:
        # No sign change: Brazil is either always cheaper or always more
        # expensive than Germany's baseline across the entire tested BR
        # WACC range, at Germany held at its own baseline WACC.
        no_frontier_direction = "always cheaper" if gap_high < 0 else "always more expensive"
        print(
            f"[competitiveness_frontier] {renewable_tech}: No frontier found "
            f"for {renewable_tech} -- Brazil {no_frontier_direction} than "
            f"Germany across all tested WACC combinations."
        )
    else:
        parity_br_wacc = brentq(_gap, br_range.min, br_range.max, xtol=1e-6)
        closure_value = lcoh_br_baseline - _lcoh_for(
            Region.NORDESTE_BR, config, renewable_tech=renewable_tech,
            wacc_override=parity_br_wacc, scenario="baseline",
        )
        gap_pp = (baseline_br_wacc - parity_br_wacc) * 100.0
        if parity_br_wacc <= baseline_br_wacc:
            # Brazil starts more expensive at baseline (gap_low > 0):
            # closure_value > 0, WACC must fall to reach parity -- the
            # conventional "gap to close" framing this metric was designed
            # for, exact requested wording.
            print(
                f"[competitiveness_frontier] WACC-gap closure: reducing BR WACC "
                f"from {baseline_br_wacc:.2%} to {parity_br_wacc:.2%} "
                f"(delta {gap_pp:.2f} pp) lowers LCOH by ${closure_value:.2f}/kg"
            )
        else:
            # Brazil already CHEAPER than Germany at baseline (gap_low < 0):
            # there is no gap to close -- parity_br_wacc is instead the
            # ceiling Brazil's WACC could rise to before losing its edge.
            # closure_value is negative here (LCOH would RISE, not fall) --
            # reporting that honestly rather than the misleading "lowers
            # LCOH by $-X/kg" the literal template would otherwise print.
            print(
                f"[competitiveness_frontier] WACC headroom: Brazil is already "
                f"cheaper than Germany at baseline WACC ({baseline_br_wacc:.2%}); "
                f"BR WACC could rise to {parity_br_wacc:.2%} (delta {-gap_pp:.2f} pp) "
                f"before losing that edge, at which point LCOH would rise by "
                f"${-closure_value:.2f}/kg to match Germany's baseline"
            )
        print(
            f"[competitiveness_frontier] Competitiveness frontier for "
            f"{renewable_tech}: parity when WACC_BR <= {parity_br_wacc:.2%} "
            f"vs WACC_DE >= {baseline_de_wacc:.2%} (baseline: BR "
            f"{baseline_br_wacc:.2%}, DE {baseline_de_wacc:.2%})"
        )

    return {
        "technology": renewable_tech,
        "baseline_br_wacc": baseline_br_wacc,
        "baseline_de_wacc": baseline_de_wacc,
        "lcoh_br_baseline": lcoh_br_baseline,
        "lcoh_de_baseline": lcoh_de_baseline,
        "direct_support_usd_per_kg": direct_support,
        "direct_support_by_scenario_usd_per_kg": direct_support_result["by_scenario_usd_per_kg"],
        "direct_support_range_min_usd_per_kg": direct_support_result["range_min_usd_per_kg"],
        "direct_support_range_max_usd_per_kg": direct_support_result["range_max_usd_per_kg"],
        "direct_support_interpretation": direct_support_result["interpretation"],
        "gap_closure_parity_br_wacc": parity_br_wacc,
        "gap_closure_value_usd_per_kg": closure_value,
        "no_frontier_direction": no_frontier_direction,
        "n_parity_grid_points": int(frontier_df["parity_flag"].sum()) if not frontier_df.empty else 0,
    }


def run_all_frontiers(config) -> dict:
    """
    Runs compute_frontier() + summarize_frontier() for both renewable
    technologies. Called once from run_pipeline.py's stage_economics(),
    after all three combined scenarios have completed -- not from
    decomposition.py itself.
    """
    results = {}
    for renewable_tech in ("solar_pv", "onshore_wind"):
        frontier_df = compute_frontier(config, renewable_tech)
        summary = summarize_frontier(config, renewable_tech, frontier_df)
        results[renewable_tech] = {"frontier": frontier_df, "summary": summary}
    return results


if __name__ == "__main__":
    from config.config_loader import load_scenario_config

    cfg = load_scenario_config()
    run_all_frontiers(cfg)
