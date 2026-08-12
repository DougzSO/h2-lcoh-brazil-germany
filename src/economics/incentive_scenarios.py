"""
src/economics/incentive_scenarios.py

Runs the two REHIDRO/IPCEI Hy2Use-labelled scenarios described in
METHODOLOGY.md section 2.8 against each region's own actual-conditions,
zero-incentive baseline (decomposition.decompose_actual), and -- since
ADR-013 -- a transparent "incentive equivalence" analysis
(summarize_incentive_equivalence()) that replaces the earlier practice of
treating REHIDRO/IPCEI as if they were flat per-kg production credits.

Neither REHIDRO (Brazil) nor IPCEI/H2Global/CCfD (Germany) is a per-kg
production subsidy in reality (see docs/memory/05_economic_model.md and
METHODOLOGY.md's "Incentive Landscape and Policy Implications"
subsection): REHIDRO is a CAPEX-side tax suspension, PHBC is not yet
operational, and Germany's mechanisms are investment/offtake instruments,
not production credits. `region_cfg.incentives.production_credit_usd_per_kg`
is therefore 0.0 for both regions (never a fabricated non-zero
placeholder) -- run_rehidro_scenario()/run_ipcei_scenario() below still
run and still call lcoh_model.calculate_lcoh() with that configured
(zero) credit, so their LCOH output is bit-for-bit the zero-incentive
baseline, honestly reflecting that no such credit exists today.
summarize_incentive_equivalence() is the actual answer to "what WOULD it
take": it reuses competitiveness_frontier.direct_support_range()'s
already-computed LCOH gap rather than re-deriving or inventing a number.

Unlike decomposition.py's _lcoh_for (which always forces incentive=0 by
design), run_rehidro_scenario()/run_ipcei_scenario() apply whatever
production incentive is configured (0.0 today) via lcoh_model directly.
They reuse decomposition.py's private region/WACC/LCOE/electrolyzer
resolution helpers rather than reimplementing them, and call
lcoh_model.calculate_lcoh() directly with an explicit
incentive_value_usd_per_kg / incentive_duration_years read from
region_cfg.incentives -- never hardcoded here (CLAUDE.md rule 4).
"""

from __future__ import annotations

import os

import pandas as pd

from src.core.constants import Region
from src.economics.competitiveness_frontier import direct_support_range
from src.economics.decomposition import (
    _electrolyzer_defaults,
    _region_config,
    _renewable_capacity_factor,
    _renewable_lcoe,
    _tech_wacc,
    decompose_actual,
)
from src.economics.lcoh_model import calculate_lcoh

_RENEWABLE_TECH_DEFAULT = "solar_pv"
_RENEWABLE_TECHS = ("solar_pv", "onshore_wind")

OUTPUT_DIR = "outputs/tables"
INCENTIVE_SCENARIOS_CSV = os.path.join(OUTPUT_DIR, "incentive_scenarios.csv")


def _lcoh_with_incentive(
    region: Region,
    config,
    incentive_value_usd_per_kg: float,
    incentive_duration_years: float,
    renewable_tech: str = _RENEWABLE_TECH_DEFAULT,
) -> float:
    """Compute LCOH for `region` under its own actual WACC and CAPEX
    multiplier, with an explicit, non-zero production incentive applied.

    Mirrors decomposition._lcoh_for's parameter resolution (same WACC,
    CAPEX multiplier, capacity-factor-bounded full-load-hours convention),
    but -- unlike that function -- passes the given incentive through to
    calculate_lcoh() instead of forcing it to zero.
    """
    region_cfg = _region_config(region, config)
    base = _electrolyzer_defaults(config, "pem")

    wacc = _tech_wacc(region_cfg, renewable_tech)
    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech)
    lcoe_usd_per_kwh = _renewable_lcoe(config, region_cfg, renewable_tech, wacc, capacity_factor)

    full_load_hours = capacity_factor * 8760.0
    hydrogen_output_kg_per_kw_year = full_load_hours / base["efficiency_kwh_per_kg"]

    return calculate_lcoh(
        capex_usd_per_kw=base["capex_usd_per_kw"],
        opex_pct_of_capex=base["opex_pct_of_capex"],
        wacc=wacc,
        lifetime_years=base["lifetime_years"],
        hydrogen_output_kg_per_kw_year=hydrogen_output_kg_per_kw_year,
        stack_replacement_pct_of_capex=base["stack_replacement_pct_of_capex"],
        stack_lifetime_hours=base["stack_lifetime_hours"],
        full_load_hours=full_load_hours,
        water_cost_usd_per_m3=base["water_cost_usd_per_m3"],
        water_consumption_l_per_kg=base["water_consumption_l_per_kg"],
        lcoe_usd_per_kwh=lcoe_usd_per_kwh,
        capex_multiplier=region_cfg.capex_multiplier,
        incentive_value_usd_per_kg=incentive_value_usd_per_kg,
        incentive_duration_years=incentive_duration_years,
    )


def run_rehidro_scenario(region: Region, config) -> dict:
    """LCOH for Northeast Brazil under the REHIDRO production credit.

    Brazil only -- raises ValueError for any other region.
    """
    if region != Region.NORDESTE_BR:
        raise ValueError(f"run_rehidro_scenario is Brazil-only, got region={region!r}")

    region_cfg = _region_config(region, config)
    incentive_value = region_cfg.incentives["production_credit_usd_per_kg"]
    incentive_duration = region_cfg.incentives["support_period_years"]

    lcoh_value = _lcoh_with_incentive(region, config, incentive_value, incentive_duration)

    print(
        f"[incentive_scenarios] rehidro | {region.value}: "
        f"LCOH = {lcoh_value:.3f} USD/kg "
        f"(credit={incentive_value:.2f} USD/kg x {incentive_duration:.0f} yr)"
    )

    return {
        "scenario": "rehidro",
        "region": region.value,
        "incentive_value_usd_per_kg": incentive_value,
        "incentive_duration_years": incentive_duration,
        "lcoh_usd_per_kg": lcoh_value,
    }


def run_ipcei_scenario(region: Region, config) -> dict:
    """LCOH for North Germany under the IPCEI Hy2Use-consistent credit.

    Germany only -- raises ValueError for any other region.
    """
    if region != Region.NORTH_GERMANY:
        raise ValueError(f"run_ipcei_scenario is Germany-only, got region={region!r}")

    region_cfg = _region_config(region, config)
    incentive_value = region_cfg.incentives["production_credit_usd_per_kg"]
    incentive_duration = region_cfg.incentives["support_period_years"]

    lcoh_value = _lcoh_with_incentive(region, config, incentive_value, incentive_duration)

    print(
        f"[incentive_scenarios] ipcei_hy2use | {region.value}: "
        f"LCOH = {lcoh_value:.3f} USD/kg "
        f"(credit={incentive_value:.2f} USD/kg x {incentive_duration:.0f} yr)"
    )

    return {
        "scenario": "ipcei_hy2use",
        "region": region.value,
        "incentive_value_usd_per_kg": incentive_value,
        "incentive_duration_years": incentive_duration,
        "lcoh_usd_per_kg": lcoh_value,
    }


def summarize_incentive_equivalence(config, renewable_tech: str) -> dict:
    """
    Transparent "incentive equivalence" analysis (ADR-013), replacing the
    earlier practice of running REHIDRO/IPCEI as if they were flat per-kg
    production credits. Reuses
    competitiveness_frontier.direct_support_range()'s already-computed
    baseline LCOH gap for `renewable_tech` -- never re-derives or invents a
    number -- and reports what a hypothetical per-kg credit would need to
    be to close it, contrasted with what Brazil's and Germany's real
    programmes actually are.

    Brazil side: if the baseline gap is positive (Brazil more expensive),
    prints the exact requested "would need a production credit equivalent
    to $Z.ZZ/kg" framing. If the gap is zero or negative (Brazil already
    cheaper, as it currently is for solar), that framing would be false --
    prints an honest "no credit needed" statement instead rather than
    forcing the sign to match the requested template.

    Germany side: deliberately does NOT print a fabricated "$W.WW/kg
    equivalent" figure for IPCEI/H2Global/CCfD's combined indirect cost
    reduction. None of the three programmes publishes a per-kg-hydrogen
    cost-reduction equivalent, and none of this pipeline's inputs (WACC,
    CAPEX, capacity factor) is decomposed into "the portion attributable
    to policy support" versus "the portion attributable to market
    conditions" -- inventing a number here would violate the explicit
    "do not invent fake incentive values" constraint this analysis was
    built under. The message instead states plainly that no such figure
    is quantifiable from public program data, rather than silently
    omitting the German side or fabricating a value to fill the template.
    """
    gap = direct_support_range(config, renewable_tech)
    baseline_gap = gap["baseline_usd_per_kg"]

    if baseline_gap > 0:
        brazil_message = (
            f"To close a ${baseline_gap:.2f}/kg LCOH gap, Brazil would need a "
            f"production credit equivalent to ${baseline_gap:.2f}/kg — "
            f"REHIDRO/PHBC do not currently provide this form of support."
        )
    else:
        brazil_message = (
            f"Brazil already has a ${-baseline_gap:.2f}/kg LCOH advantage over "
            f"Germany for {renewable_tech} at baseline; no production credit is "
            f"needed to reach parity, and REHIDRO/PHBC do not provide one "
            f"regardless."
        )

    germany_message = (
        "Germany's existing support ecosystem (IPCEI + H2Global + CCfD) "
        "provides investment and offtake support, but none of the three "
        "programmes publishes a per-kg-hydrogen cost-reduction equivalent, "
        "and this study does not decompose Germany's modeled WACC/CAPEX "
        "into a policy-attributable share -- no per-kg equivalent figure "
        "is reported here, in order to avoid inventing one."
    )

    print(f"[incentive_scenarios] {renewable_tech} equivalence | brazil: {brazil_message}")
    print(f"[incentive_scenarios] {renewable_tech} equivalence | germany: {germany_message}")

    return {
        "technology": renewable_tech,
        "baseline_gap_usd_per_kg": baseline_gap,
        "brazil_equivalence_message": brazil_message,
        "germany_equivalence_message": germany_message,
    }


def run_all_incentive_scenarios(config) -> dict:
    """Baseline (zero-incentive, via decomposition.decompose_actual) and
    incentive-adjusted LCOH for both regions -- credit=0.0 today (see
    module docstring), so *_lcoh_usd_per_kg below is bit-for-bit the
    zero-incentive baseline, not a fabricated reduction. Persists this
    part of the result to outputs/tables/incentive_scenarios.csv (one row
    per region, UNCHANGED schema) before returning, so viz/plotting.py's
    generate_all_plots() can find it and render
    outputs/figures/incentive_scenarios.png without requiring a caller to
    pass the dict through by hand.

    Also runs summarize_incentive_equivalence() (ADR-013) for both
    renewable technologies, appended to the returned dict under
    "incentive_equivalence" -- NOT written to incentive_scenarios.csv,
    to avoid changing that file's consumed-by-viz schema.
    """
    brazil_baseline = decompose_actual(Region.NORDESTE_BR, config)
    germany_baseline = decompose_actual(Region.NORTH_GERMANY, config)

    brazil_rehidro = run_rehidro_scenario(Region.NORDESTE_BR, config)
    germany_ipcei = run_ipcei_scenario(Region.NORTH_GERMANY, config)

    result = {
        "brazil": {
            "baseline_lcoh_usd_per_kg": brazil_baseline["lcoh_usd_per_kg"],
            "rehidro_lcoh_usd_per_kg": brazil_rehidro["lcoh_usd_per_kg"],
        },
        "germany": {
            "baseline_lcoh_usd_per_kg": germany_baseline["lcoh_usd_per_kg"],
            "ipcei_lcoh_usd_per_kg": germany_ipcei["lcoh_usd_per_kg"],
        },
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    rows = [dict(region=region, **values) for region, values in result.items()]
    pd.DataFrame(rows).to_csv(INCENTIVE_SCENARIOS_CSV, index=False)
    print(f"[incentive_scenarios] wrote {INCENTIVE_SCENARIOS_CSV}")

    result["incentive_equivalence"] = {
        tech: summarize_incentive_equivalence(config, tech) for tech in _RENEWABLE_TECHS
    }

    return result


if __name__ == "__main__":
    from config.config_loader import load_scenario_config

    cfg = load_scenario_config()
    print(run_all_incentive_scenarios(cfg))
