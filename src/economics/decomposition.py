"""
src/economics/decomposition.py

Runs the comparative experiments for the WACC-vs-resource-quality study:
(1) actual LCOH per region, (2) WACC-swap counterfactual, and (3) the
combined min/baseline/max scenario LCOH range (run_scenario_range()). The
1D Brazil-only inversion-point search that used to live here has been
replaced by the 2D WACC_BR x WACC_DE competitiveness frontier in the
sibling module `competitiveness_frontier.py` (see ADR-009,
docs/memory/06_technical_decisions_log.md).

WACC is technology-specific, not a regional average: each run is tied to a
single renewable technology (`renewable_tech`, "solar_pv" or "onshore_wind"),
and uses THAT technology's WACC, resolved from region_cfg.wacc.solar/.wind
(a literature-validated {min, baseline, max} RangeParam, see _tech_wacc())
at the given combined scenario branch, for both the renewable LCOE
calculation and the electrolyzer discounting. Electricity cost is included
via `lcoh_model.calculate_lcoe`, fed into `calculate_lcoh`'s required
`lcoe_usd_per_kwh` argument.

Base electrolyzer technology is PEM. Alkaline is a sensitivity-only variant
(see sensitivity_analysis.py) and is never used in these decomposition runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import geopandas as gpd
import numpy as np
import pandas as pd

from config.config_loader import resolve_inverted_param, resolve_param
from src.core.constants import Region
from src.economics.lcoh_model import calculate_lcoh, calculate_lcoe

OUTPUT_DIR = "outputs/tables"
DECOMPOSITION_CSV = os.path.join(OUTPUT_DIR, "decomposition.csv")
INPUT_ASSUMPTIONS_CSV = os.path.join(OUTPUT_DIR, "input_assumptions.csv")

# Monte Carlo uncertainty quantification (run_lcoh_monte_carlo(),
# compute_lcoh_with_uncertainty()) -- see docs/memory/09_methodology_assumptions.md.
MC_SAMPLES = 1000
MC_CONFIDENCE_LEVEL = 0.95
MC_CAPEX_RELATIVE_STD = 0.15  # Normal(baseline, 15% relative std)
MC_OPEX_RELATIVE_STD = 0.10   # Normal(baseline, 10% relative std)
MC_WACC_HALF_WIDTH_PP = 0.02  # Triangular(baseline -2pp, baseline, baseline +2pp)
MC_DEFAULT_SEED = 42          # fixed for reproducibility across runs, overridable

# potential.h2_potential.calculate_h2_potential()'s output directory --
# read here, never written (Hard Rule 7, root CLAUDE.md: each stage writes
# to disk before the next stage reads).
H2_POTENTIAL_DIR = Path("data/processed")

# site_selection.py/h2_potential.py use "solar"/"wind" for the siting
# technology; this module's renewable_tech vocabulary is "solar_pv"/
# "onshore_wind" (matches config.technologies'/config.renewables' own
# attribute names directly).
_TECH_TO_RENEWABLE_TECH: Dict[str, str] = {"solar": "solar_pv", "wind": "onshore_wind"}

# Region enum values ("nordeste_br", "north_germany") do NOT match the
# attribute names on ScenarioConfig.regions ("brazil", "germany"). This
# mapping is a deliberate, explicit patch for that mismatch. The proper
# fix is to align the Enum values with the YAML keys at the source.
_REGION_TO_CONFIG_ATTR: Dict[Region, str] = {
    Region.NORDESTE_BR: "brazil",
    Region.NORTH_GERMANY: "north_germany".replace("north_germany", "germany"),
}


def _region_config(region: Region, config):
    """Resolve the RegionConfig object for a given Region enum member."""
    attr_name = _REGION_TO_CONFIG_ATTR[region]
    return getattr(config.regions, attr_name)


def _tech_wacc(region_cfg, renewable_tech: str, scenario: str = "baseline") -> float:
    """
    Return the WACC tied to a specific renewable technology, not a
    region-wide average. WACC is technology-specific: solar and wind
    projects in the same region can have different financing costs.

    WACC is a literature-validated {min, baseline, max} RangeParam
    (RegionWaccConfig.solar/.wind), resolved via resolve_inverted_param()
    -- combined-scenario "min" (worst case) means the HIGHEST financing
    cost (the RangeParam's own `max`), "max" (best case) means the LOWEST
    (the RangeParam's own `min`). This is the inverse of every other
    RangeParam consumer in this module (capacity_factor/density: "min"
    scenario directly reads `.min`). `scenario="baseline"` reproduces the
    exact scalar value this function used to return unconditionally.
    """
    if renewable_tech == "solar_pv":
        return resolve_inverted_param(region_cfg.wacc.solar, scenario)
    if renewable_tech == "onshore_wind":
        return resolve_inverted_param(region_cfg.wacc.wind, scenario)
    raise ValueError(f"Unknown renewable_tech: {renewable_tech!r}")


_CAPEX_OVERRIDE_FIELD = {
    "solar_pv": "solar_pv_capex_override_usd_per_kw",
    "onshore_wind": "onshore_wind_capex_override_usd_per_kw",
}
_CAPEX_RANGE_OVERRIDE_FIELD = {
    "solar_pv": "solar_pv_capex_range_override_usd_per_kw",
    "onshore_wind": "onshore_wind_capex_range_override_usd_per_kw",
}


def _effective_renewable_capex(region_cfg, renewable_cfg, renewable_tech: str) -> float:
    """
    Renewable CAPEX (USD/kW) for this region+technology: region_cfg's own
    <tech>_capex_override_usd_per_kw if set for this renewable_tech,
    otherwise the shared renewables.<tech>.capex_usd_per_kw baseline used
    by every other region+technology pair. RenewablesConfig itself has no
    region axis, so this override is the ONLY mechanism by which one
    region's renewable CAPEX can differ from another's -- reused by every
    caller below (and by sensitivity_analysis.py's own _renewable_lcoe())
    so they cannot silently diverge onto two different CAPEX values for
    the same region+technology. Brazil sets onshore_wind's override
    (IRENA/EPE competitive Northeast benchmark) and, since ADR-012,
    solar_pv's too -- not to shift Brazil's solar CAPEX, but to preserve it
    unchanged after renewables.solar_pv's shared value was updated for
    Germany (ABUZAYED & HARTMANN, 2022; see docs/memory/05_economic_model.md).
    """
    override_field = _CAPEX_OVERRIDE_FIELD.get(renewable_tech)
    if override_field is not None:
        override = getattr(region_cfg, override_field, None)
        if override is not None:
            return override
    return renewable_cfg.capex_usd_per_kw


def _effective_renewable_capex_for_scenario(
    region_cfg, renewable_cfg, renewable_tech: str, scenario: str
) -> float:
    """
    Renewable CAPEX (USD/kW) for this region+technology at a given COMBINED
    scenario branch. "baseline" delegates unchanged to
    _effective_renewable_capex() above (preserves any region-specific
    single-value override exactly). Non-baseline branches read
    renewable_cfg.capex_range -- unless region_cfg has a
    <tech>_capex_range_override_usd_per_kw set for this renewable_tech
    (Brazil only today, for both onshore_wind and, since ADR-012,
    solar_pv -- see docs/memory/05_economic_model.md).

    Combined "min" (worst case) -> the range's own `max` (highest CAPEX);
    combined "max" (best case) -> the range's own `min` (lowest CAPEX) --
    same inversion direction as WACC (see _tech_wacc), since higher CAPEX
    is worse. capex_multiplier is NOT applied here and is untouched by this
    function: it keeps applying only to electrolyzer CAPEX inside
    calculate_lcoh(), independent of this renewable-CAPEX scenario
    variation, exactly as it does today (calculate_lcoe() has no
    capex_multiplier parameter at all).
    """
    if scenario == "baseline":
        return _effective_renewable_capex(region_cfg, renewable_cfg, renewable_tech)

    capex_range = renewable_cfg.capex_range
    range_override_field = _CAPEX_RANGE_OVERRIDE_FIELD.get(renewable_tech)
    if range_override_field is not None:
        override_range = getattr(region_cfg, range_override_field, None)
        if override_range is not None:
            capex_range = override_range

    low, high = capex_range
    if scenario == "min":
        return high
    if scenario == "max":
        return low
    raise ValueError(f"scenario must be one of 'min'/'baseline'/'max', got {scenario!r}")


def _renewable_lcoe(
    config,
    region_cfg,
    renewable_tech: str,
    wacc: float,
    capacity_factor: float,
    renewable_capex_override: Optional[float] = None,
) -> float:
    """
    Compute the renewable LCOE (USD/kWh) for the given technology, using
    the WACC tied to that same technology (see _tech_wacc) and the SAME
    resolved capacity_factor the caller also uses to size the
    electrolyzer's full_load_hours (see _renewable_capacity_factor) --
    the renewable's own annual energy yield and the electrolyzer's own
    operating hours must use one consistent capacity factor, or the two
    halves of the LCOH calculation would silently assume two different
    resource qualities for the same technology in the same region.

    renewable_capex_override : Optional[float]
        When given, used directly as the renewable CAPEX instead of calling
        _effective_renewable_capex() -- lets _lcoh_for() feed a combined-
        scenario-resolved CAPEX (see _effective_renewable_capex_for_scenario)
        without changing this function's own baseline-path default.
    """
    renewable_cfg = getattr(config.renewables, renewable_tech)
    capex_usd_per_kw = (
        renewable_capex_override
        if renewable_capex_override is not None
        else _effective_renewable_capex(region_cfg, renewable_cfg, renewable_tech)
    )
    return calculate_lcoe(
        capex_usd_per_kw=capex_usd_per_kw,
        opex_pct_of_capex=renewable_cfg.opex_pct_of_capex,
        wacc=wacc,
        lifetime_years=renewable_cfg.lifetime_years,
        capacity_factor=capacity_factor,
    )


def _technology_region_params(config, region: Region, renewable_tech: str):
    """Resolve config.technologies.<renewable_tech>.<brazil|germany> --
    the literature-validated {min, baseline, max} range triple
    (capacity_factor, full_load_hours, capacity_density_mw_per_km2) for
    this region+technology pair. Single point of access for the three
    resolver functions below."""
    region_key = _REGION_TO_CONFIG_ATTR[region]
    tech_cfg = getattr(config.technologies, renewable_tech)
    return getattr(tech_cfg, region_key)


def _renewable_capacity_factor(
    config, region: Region, renewable_tech: str, scenario: str = "baseline"
) -> float:
    """
    Return the capacity factor to use for `renewable_tech` in `region` --
    the physical ceiling on how many hours per year the electrolyzer can
    actually run, since it has no other power source (METHODOLOGY.md
    2.4's "dedicated electrolysis, no competing grid off-take" assumption;
    config has no separate "electrolyzer full-load hours" field, nor
    should it -- the electrolyzer's own operating hours are physically
    bounded by the renewable's production profile, not an independent
    parameter).

    Resolved from config.technologies.<renewable_tech>.<region>.
    capacity_factor (a literature-validated {min, baseline, max} range,
    scenario_params.yaml -- see docs/memory/09_methodology_assumptions.md)
    via resolve_param() at the given combined-scenario branch (default
    "baseline" -- the pipeline no longer has a single global scenario
    selector; every caller either passes its own combined-scenario branch
    explicitly or accepts baseline). Every region+technology pair has its
    own explicit range -- no shared/default fallback exists in this schema.

    Resolving this at all here, rather than defaulting to
    capacity_factor=1.0 (an always-on, 8760h/yr electrolyzer with no
    power-supply constraint, physically impossible for a dedicated
    renewable off-take), is the fix for the bug _lcoh_for()'s docstring
    documents.
    """
    params = _technology_region_params(config, region, renewable_tech)
    return resolve_param(params.capacity_factor, scenario)


def _renewable_full_load_hours(
    config, region: Region, renewable_tech: str, scenario: str = "baseline"
) -> float:
    """
    Return full_load_hours (h/yr) for `renewable_tech` in `region`,
    resolved directly from config.technologies.<renewable_tech>.<region>.
    full_load_hours rather than recomputing capacity_factor * 8760.0 --
    config_loader.py's TechRegionParams validator guarantees the two agree
    at load time (see its check_full_load_hours_consistency), so reading
    either one is equivalent; this avoids every caller repeating the same
    multiplication.
    """
    params = _technology_region_params(config, region, renewable_tech)
    return resolve_param(params.full_load_hours, scenario)


def _renewable_capacity_density(
    config, region: Region, renewable_tech: str, scenario: str = "baseline"
) -> float:
    """
    Return installable capacity density (MW/km2) for `renewable_tech` in
    `region`, resolved from config.technologies.<renewable_tech>.<region>.
    capacity_density_mw_per_km2 -- see
    docs/memory/10_capacity_density_assumptions.md for the literature
    review. Now region-specific in the schema (both regions currently
    carry the same NREL/standard-spacing figures per technology, a
    deliberate study-specific choice, not a schema limitation -- unlike
    the pre-migration renewables.<tech>.power_density_w_per_m2 scalar,
    which had no region axis at all).
    """
    params = _technology_region_params(config, region, renewable_tech)
    return resolve_param(params.capacity_density_mw_per_km2, scenario)


def _electrolyzer_defaults(config, technology: str = "pem") -> dict:
    """
    Extract electrolyzer + shared parameters needed by calculate_lcoh.
    Defaults to the PEM technology; alkaline can be substituted by callers.
    """
    tech_cfg = config.electrolyzer.technologies[technology]
    shared_cfg = config.electrolyzer.shared

    # Annual H2 output per kW: efficiency is kWh consumed per kg produced,
    # so annual kWh delivered = full_load_hours * 1 kW = full_load_hours kWh.
    # kg produced per year = full_load_hours / efficiency_kwh_per_kg.
    return {
        "capex_usd_per_kw": tech_cfg.capex_usd_per_kw,
        "opex_pct_of_capex": shared_cfg.opex_pct_of_capex,
        "lifetime_years": shared_cfg.lifetime_years,
        "stack_replacement_pct_of_capex": tech_cfg.stack_replacement_pct_of_capex,
        "stack_lifetime_hours": tech_cfg.stack_lifetime_hours,
        "water_cost_usd_per_m3": shared_cfg.water_cost_usd_per_m3,
        "water_consumption_l_per_kg": shared_cfg.water_consumption_l_per_kg,
        "efficiency_kwh_per_kg": tech_cfg.efficiency_kwh_per_kg,
    }


def _lcoh_for(
    region: Region,
    config,
    renewable_tech: str = "solar_pv",
    wacc_override: float = None,
    scenario: str = "baseline",
    technology: str = "pem",
) -> float:
    """
    Internal helper: compute LCOH for a region + renewable technology pair.

    scenario : str
        The COMBINED scenario branch ("min"/"baseline"/"max", default
        "baseline") -- resolves WACC, capacity_factor/full_load_hours,
        capacity_density (via h2_potential.py, not this function directly),
        and renewable CAPEX all at the SAME branch simultaneously (see
        scenario_params.yaml's top-of-file comment and
        docs/memory/05_economic_model.md). Ignored for whichever of
        WACC/renewable-CAPEX is separately overridden below.

    WACC is technology-specific (see _tech_wacc): unless wacc_override is
    given, the WACC used for BOTH the renewable LCOE and the electrolyzer
    discounting is resolved from region_cfg.wacc.solar/.wind at `scenario`.
    capex_multiplier is ALWAYS taken from region_cfg (never overridden here)
    — callers that need a WACC-swap counterfactual must pass wacc_override
    and rely on region_cfg's own capex_multiplier remaining untouched.
    wacc_override takes precedence over `scenario`'s own WACC resolution --
    used by decompose_wacc_swap() and competitiveness_frontier.py to sweep
    WACC independently of any RangeParam branch, while every other
    parameter (CF, density, renewable CAPEX, OPEX, lifetime, electrolyzer)
    stays pinned at `scenario`'s own branch (baseline by default).

    Capacity factor/full_load_hours are always resolved from
    renewable_tech's OWN config.technologies.<tech>.<region>.capacity_factor
    at `scenario` (see _renewable_capacity_factor()) -- a dedicated
    electrolyzer with no competing grid off-take (METHODOLOGY.md 2.4) can
    only run when its own renewable source is producing, so this is never
    defaulted to 1.0/8760h.
    """
    region_cfg = _region_config(region, config)
    renewable_cfg = getattr(config.renewables, renewable_tech)
    base = _electrolyzer_defaults(config, technology)

    wacc = wacc_override
    if wacc is None:
        wacc = _tech_wacc(region_cfg, renewable_tech, scenario=scenario)

    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech, scenario=scenario)
    # Read directly rather than capacity_factor * 8760.0 -- guaranteed
    # consistent with capacity_factor by config_loader.py's
    # TechRegionParams validator (see _renewable_full_load_hours()).
    full_load_hours = _renewable_full_load_hours(config, region, renewable_tech, scenario=scenario)

    renewable_capex = _effective_renewable_capex_for_scenario(
        region_cfg, renewable_cfg, renewable_tech, scenario
    )
    lcoe_usd_per_kwh = _renewable_lcoe(
        config, region_cfg, renewable_tech, wacc, capacity_factor,
        renewable_capex_override=renewable_capex,
    )

    hydrogen_output_kg_per_kw_year = full_load_hours / base["efficiency_kwh_per_kg"]

    # Baseline decomposition (decompose_actual / decompose_wacc_swap /
    # run_scenario_range / the 2D frontier all route through this helper)
    # must be computed with ZERO policy incentive, per METHODOLOGY.md §2.5
    # -- region_cfg's incentives (e.g. a production credit) are
    # deliberately NOT read here, so they can no longer silently contaminate
    # the baseline. Applying real incentive values is the job of
    # economics/incentive_scenarios.py, which calls calculate_lcoh directly
    # with its own incentive_value/duration rather than through _lcoh_for.
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
        incentive_value_usd_per_kg=0.0,
        incentive_duration_years=0.0,
    )


def run_scenario_range(region: Region, config, renewable_tech: str = "solar_pv") -> dict:
    """
    Explicit COMBINED min/baseline/max sweep for one region+tech: WACC,
    capacity_factor/full_load_hours, and renewable CAPEX all move together
    at each branch (see scenario_params.yaml's top-of-file comment) via
    _lcoh_for()'s `scenario` parameter -- not independent one-at-a-time
    corners. Reuses _lcoh_for() -> calculate_lcoh() as-is for every branch,
    no LCOH math duplicated; does not touch run_lcoh_monte_carlo()/
    compute_lcoh_with_uncertainty() at all.

    True range is min/max of the 3 resolved LCOH values themselves, not
    assumed monotonic in scenario branch.
    """
    region_cfg = _region_config(region, config)
    renewable_cfg = getattr(config.renewables, renewable_tech)

    lcoh = {
        s: _lcoh_for(region, config, renewable_tech=renewable_tech, scenario=s)
        for s in ("min", "baseline", "max")
    }
    low, high, base = min(lcoh.values()), max(lcoh.values()), lcoh["baseline"]
    width_pct = 100.0 * (high - low) / base if base else float("nan")

    print(
        f"[decomposition] {region.value}/{renewable_tech}: LCOH = "
        f"{low:.2f}–{high:.2f} USD/kg (baseline {base:.2f}, range {width_pct:.1f}%)"
    )

    rows = [
        {
            "run_type": "scenario_range", "region": region.value, "renewable_tech": renewable_tech,
            "scenario": s,
            "wacc_used": _tech_wacc(region_cfg, renewable_tech, scenario=s),
            "capex_multiplier_used": region_cfg.capex_multiplier,
            "renewable_capex_used": _effective_renewable_capex_for_scenario(
                region_cfg, renewable_cfg, renewable_tech, s
            ),
            "lcoh_usd_per_kg": lcoh[s],
        }
        for s in ("min", "baseline", "max")
    ]
    rows.append({
        "run_type": "range_summary", "region": region.value, "renewable_tech": renewable_tech,
        "lcoh_min": low, "lcoh_baseline": base, "lcoh_max": high,
        "lcoh_range_str": f"{low:.2f}–{high:.2f}", "range_width_pct": width_pct,
    })
    return {"rows": rows, "lcoh_min": low, "lcoh_baseline": base, "lcoh_max": high, "range_width_pct": width_pct}


def run_lcoh_monte_carlo(
    region: Region,
    config,
    renewable_tech: str = "solar_pv",
    technology: str = "pem",
    n_samples: int = MC_SAMPLES,
    seed: Optional[int] = MC_DEFAULT_SEED,
) -> dict:
    """
    Monte Carlo uncertainty quantification around _lcoh_for()'s
    deterministic baseline LCOH (that function itself, and
    lcoh_model.calculate_lcoh()/calculate_lcoe(), are NOT modified --
    every sample below is computed by calling those same pure functions
    with sampled inputs, never by re-deriving the LCOH formula).

    Sampled parameters (independent draws -- correlation between them,
    e.g. CAPEX and WACC both rising in a high-financing-cost regime, is a
    real simplification, documented here rather than silently assumed
    away):
      - electrolyzer CAPEX (config.electrolyzer.technologies[technology].
        capex_usd_per_kw) and renewable CAPEX (config.renewables.
        <renewable_tech>.capex_usd_per_kw): each Normal(baseline, 15%
        relative std) -- both are real capital-cost terms feeding LCOH
        (electrolyzer directly, renewable via calculate_lcoe()), so both
        are sampled, not just one.
      - electrolyzer OPEX% and renewable OPEX% (opex_pct_of_capex on each
        side): each Normal(baseline, 10% relative std).
      - WACC: Triangular(baseline - 2pp, baseline, baseline + 2pp),
        technology-specific (region_cfg.wacc.solar/.wind via _tech_wacc) --
        the SAME wacc used for both the renewable LCOE and the
        electrolyzer discounting, exactly as the deterministic path does.
      - capacity factor: Uniform(min, max), read directly from
        config.technologies.<renewable_tech>.<region>.capacity_factor's
        own {min, max} -- never a hardcoded band. full_load_hours for each
        sample is capacity_factor_sample * 8760.0 (the same relationship
        config_loader.py's TechRegionParams validator enforces for the
        deterministic baseline).
      - capacity density: Uniform(min, max), read directly from
        config.technologies.<renewable_tech>.<region>.
        capacity_density_mw_per_km2's own {min, max} per this function's
        own spec -- sampled for completeness, but NOT propagated into any
        LCOH sample below: calculate_lcoh()/calculate_lcoe() take no
        capacity/area/density argument at all (LCOH is a per-kW cost
        ratio, independent of total installed scale -- capacity density
        instead determines total installable_capacity_mw/
        annual_h2_production_t in potential/h2_potential.py, a materially
        different output). Verified: perturbing this parameter alone
        leaves every lcoh_samples value unchanged to floating-point
        precision.

    Parameters
    ----------
    seed : Optional[int]
        Fixed by default (MC_DEFAULT_SEED) for reproducibility across
        pipeline runs -- a research artifact whose confidence intervals
        should not change from one `python run_pipeline.py` invocation to
        the next. Pass None for non-reproducible sampling, or an explicit
        int to decorrelate multiple calls.

    Returns
    -------
    dict
        lcoh_mean, lcoh_std (sample std, ddof=1), lcoh_ci_lower,
        lcoh_ci_upper (2.5th/97.5th percentiles for the default 95% CI),
        n_samples, lcoh_samples (np.ndarray, full distribution).
    """
    rng = np.random.default_rng(seed)

    region_cfg = _region_config(region, config)
    base = _electrolyzer_defaults(config, technology)
    renewable_cfg = getattr(config.renewables, renewable_tech)
    tech_params = _technology_region_params(config, region, renewable_tech)

    baseline_wacc = _tech_wacc(region_cfg, renewable_tech)
    wacc_samples = rng.triangular(
        baseline_wacc - MC_WACC_HALF_WIDTH_PP,
        baseline_wacc,
        baseline_wacc + MC_WACC_HALF_WIDTH_PP,
        size=n_samples,
    )

    # Normal draws clipped at a tiny positive floor / zero: a rare tail
    # draw landing at or below zero is physically meaningless for a cost
    # or a percentage, and would otherwise crash calculate_lcoh() on an
    # unattended 1000-sample run rather than reflect a real scenario.
    electrolyzer_capex_samples = np.clip(
        rng.normal(base["capex_usd_per_kw"], base["capex_usd_per_kw"] * MC_CAPEX_RELATIVE_STD, n_samples),
        1e-6, None,
    )
    electrolyzer_opex_samples = np.clip(
        rng.normal(base["opex_pct_of_capex"], base["opex_pct_of_capex"] * MC_OPEX_RELATIVE_STD, n_samples),
        0.0, None,
    )
    # Region-specific override (see _effective_renewable_capex()) if set,
    # so the MC sampling center matches the deterministic baseline exactly
    # -- otherwise Brazil onshore_wind's MC distribution would stay
    # centered on the old shared 1300 USD/kW baseline instead of its own
    # 1050 USD/kW override.
    renewable_capex_baseline = _effective_renewable_capex(region_cfg, renewable_cfg, renewable_tech)
    renewable_capex_samples = np.clip(
        rng.normal(renewable_capex_baseline, renewable_capex_baseline * MC_CAPEX_RELATIVE_STD, n_samples),
        1e-6, None,
    )
    renewable_opex_samples = np.clip(
        rng.normal(renewable_cfg.opex_pct_of_capex, renewable_cfg.opex_pct_of_capex * MC_OPEX_RELATIVE_STD, n_samples),
        0.0, None,
    )

    capacity_factor_samples = rng.uniform(
        tech_params.capacity_factor.min, tech_params.capacity_factor.max, size=n_samples
    )
    # Sampled per this function's own spec; intentionally unused below --
    # see the "capacity density" paragraph in this docstring.
    _capacity_density_samples = rng.uniform(
        tech_params.capacity_density_mw_per_km2.min,
        tech_params.capacity_density_mw_per_km2.max,
        size=n_samples,
    )

    lcoh_samples = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        capacity_factor = float(capacity_factor_samples[i])
        full_load_hours = capacity_factor * 8760.0
        wacc = float(wacc_samples[i])

        lcoe_usd_per_kwh = calculate_lcoe(
            capex_usd_per_kw=float(renewable_capex_samples[i]),
            opex_pct_of_capex=float(renewable_opex_samples[i]),
            wacc=wacc,
            lifetime_years=renewable_cfg.lifetime_years,
            capacity_factor=capacity_factor,
        )
        hydrogen_output_kg_per_kw_year = full_load_hours / base["efficiency_kwh_per_kg"]

        lcoh_samples[i] = calculate_lcoh(
            capex_usd_per_kw=float(electrolyzer_capex_samples[i]),
            opex_pct_of_capex=float(electrolyzer_opex_samples[i]),
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
            incentive_value_usd_per_kg=0.0,
            incentive_duration_years=0.0,
        )

    ci_lower_pct = (1.0 - MC_CONFIDENCE_LEVEL) / 2.0 * 100.0
    ci_upper_pct = (1.0 + MC_CONFIDENCE_LEVEL) / 2.0 * 100.0

    return {
        "lcoh_mean": float(np.mean(lcoh_samples)),
        "lcoh_std": float(np.std(lcoh_samples, ddof=1)),
        "lcoh_ci_lower": float(np.percentile(lcoh_samples, ci_lower_pct)),
        "lcoh_ci_upper": float(np.percentile(lcoh_samples, ci_upper_pct)),
        "n_samples": n_samples,
        "lcoh_samples": lcoh_samples,
    }


def compute_lcoh_with_uncertainty(
    region: Region,
    config,
    renewable_tech: str = "solar_pv",
    technology: str = "pem",
    n_samples: int = MC_SAMPLES,
    seed: Optional[int] = MC_DEFAULT_SEED,
) -> dict:
    """
    Return both the deterministic baseline LCOH (_lcoh_for(), UNCHANGED)
    and Monte Carlo uncertainty statistics (run_lcoh_monte_carlo()) for
    the same region + renewable technology pair. Does not print -- callers
    (decompose_actual()) own their own print formatting, matching this
    module's existing convention of each public function logging its own
    result line.
    """
    lcoh_baseline = _lcoh_for(region, config, renewable_tech=renewable_tech, technology=technology)
    mc = run_lcoh_monte_carlo(region, config, renewable_tech, technology, n_samples, seed)

    return {
        "region": region.value,
        "renewable_tech": renewable_tech,
        "lcoh_baseline_usd_per_kg": lcoh_baseline,
        "lcoh_mean": mc["lcoh_mean"],
        "lcoh_std": mc["lcoh_std"],
        "lcoh_ci_lower": mc["lcoh_ci_lower"],
        "lcoh_ci_upper": mc["lcoh_ci_upper"],
        "n_samples": mc["n_samples"],
    }


def get_input_assumptions(region: Region, renewable_tech: str, config) -> pd.DataFrame:
    """
    Disclose every input parameter that feeds a (region, renewable_tech)
    pair's LCOH computation via _lcoh_for()/calculate_lcoh() -- for
    manuscript disclosure (decomposition.csv reports LCOH outputs, but not
    what produced them), not used by any LCOH computation itself.

    NAMING NOTE: takes `renewable_tech` ("solar_pv"/"onshore_wind"), not
    `tech` ("solar"/"wind") -- this module's own established vocabulary
    (see _tech_wacc/_renewable_lcoe/decompose_actual etc., all
    `renewable_tech`); `tech` is only used where a function also reads a
    site_selection.py/h2_potential.py siting output file, which this one
    does not.

    Returns
    -------
    pd.DataFrame
        One row per electrolyzer technology (pem, alkaline), since the
        baseline decomposition uses PEM and sensitivity_analysis.py's
        technology sweep uses alkaline -- both sets of assumptions are
        disclosed, not just the baseline's. Columns: region, renewable_tech,
        electrolyzer_technology, capex_electrolyzer_usd_per_kw,
        capex_multiplier, wacc, lcoe_renewable_usd_per_kwh, capacity_factor,
        specific_energy_kwh_per_kg, lifetime_years, opex_pct_capex,
        stack_replacement_pct_capex, water_cost_usd_per_kg,
        incentive_credit_usd_per_kg, incentive_duration_years.
        The incentive columns disclose the region's own hypothetical
        program (REHIDRO for Brazil, IPCEI Hy2Use for Germany) -- they are
        NOT applied in this module's baseline LCOH (see _lcoh_for's
        docstring: baseline is always incentive-free); applying them is
        economics/incentive_scenarios.py's job.
    """
    region_cfg = _region_config(region, config)
    wacc = _tech_wacc(region_cfg, renewable_tech)
    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech)
    lcoe_usd_per_kwh = _renewable_lcoe(config, region_cfg, renewable_tech, wacc, capacity_factor)
    incentive_credit = region_cfg.incentives.get("production_credit_usd_per_kg", 0.0)
    incentive_duration = region_cfg.incentives.get("support_period_years", 0.0)

    rows = []
    for electrolyzer_technology in ("pem", "alkaline"):
        base = _electrolyzer_defaults(config, electrolyzer_technology)
        water_cost_usd_per_kg = (
            base["water_cost_usd_per_m3"] * base["water_consumption_l_per_kg"] / 1000.0
        )
        rows.append({
            "region": region.value,
            "renewable_tech": renewable_tech,
            "electrolyzer_technology": electrolyzer_technology,
            "capex_electrolyzer_usd_per_kw": base["capex_usd_per_kw"],
            "capex_multiplier": region_cfg.capex_multiplier,
            "wacc": wacc,
            "lcoe_renewable_usd_per_kwh": lcoe_usd_per_kwh,
            "capacity_factor": capacity_factor,
            "specific_energy_kwh_per_kg": base["efficiency_kwh_per_kg"],
            "lifetime_years": base["lifetime_years"],
            "opex_pct_capex": base["opex_pct_of_capex"],
            "stack_replacement_pct_capex": base["stack_replacement_pct_of_capex"],
            "water_cost_usd_per_kg": water_cost_usd_per_kg,
            "incentive_credit_usd_per_kg": incentive_credit,
            "incentive_duration_years": incentive_duration,
        })

    return pd.DataFrame(rows)


def decompose_actual(region: Region, config, renewable_tech: str = "solar_pv") -> dict:
    """
    Compute LCOH for a region + renewable technology pair, using that
    technology's own WACC (solar or wind — never a regional average) and
    the region's own CAPEX multiplier (the "actual" / baseline scenario).

    Also runs Monte Carlo uncertainty quantification
    (compute_lcoh_with_uncertainty()) around the SAME deterministic
    baseline computed here -- lcoh_usd_per_kg below is exactly
    _lcoh_for()'s unmodified return value, never replaced or adjusted by
    the Monte Carlo mean.
    """
    region_cfg = _region_config(region, config)
    wacc_used = _tech_wacc(region_cfg, renewable_tech)

    uncertainty = compute_lcoh_with_uncertainty(region, config, renewable_tech=renewable_tech)
    lcoh_value = uncertainty["lcoh_baseline_usd_per_kg"]

    print(
        f"[decomposition] actual | {region.value} / {renewable_tech}: "
        f"LCOH = {lcoh_value:.3f} USD/kg"
    )
    print(
        f"[decomposition] {region.value}/{renewable_tech}: LCOH = "
        f"{uncertainty['lcoh_mean']:.2f} USD/kg (95% CI: "
        f"{uncertainty['lcoh_ci_lower']:.2f}–{uncertainty['lcoh_ci_upper']:.2f})"
    )

    return {
        "run_type": "actual",
        "region": region.value,
        "renewable_tech": renewable_tech,
        "wacc_used": wacc_used,
        "capex_multiplier_used": region_cfg.capex_multiplier,
        "lcoh_usd_per_kg": lcoh_value,
        "lcoh_mean": uncertainty["lcoh_mean"],
        "lcoh_std": uncertainty["lcoh_std"],
        "lcoh_ci_lower": uncertainty["lcoh_ci_lower"],
        "lcoh_ci_upper": uncertainty["lcoh_ci_upper"],
    }


def decompose_actual_per_region_aggregated(
    region: Region, tech: str, config, scenario: str = "baseline"
) -> dict:
    """
    Compute LCOH from spatial-pipeline outputs (per-site installable
    capacity and H2 production) instead of pure config-level averages, and
    report it alongside decompose_actual()'s pure-YAML LCOH for comparison.

    For each candidate site in
    data/processed/h2_potential_{region}_{tech}.geojson (written by
    potential.h2_potential.calculate_h2_potential(), which itself reads
    spatial.site_selection.select_candidate_sites()'s output -- neither
    module is re-run or modified here, per Hard Rule 7, root CLAUDE.md):
    derives that site's own hydrogen_output_kg_per_kw_year from its
    installable_capacity_mw and annual_h2_production_t, then calls
    calculate_lcoh() with it, holding WACC, CAPEX, water cost, and the
    renewable LCOE at the region+tech's own config values -- exactly as
    decompose_actual()/_lcoh_for() do; only the H2-output term is
    site-specific. Aggregates the per-site LCOH values into a single
    area-weighted mean (weighted by suitable_area_km2).

    WHY THE TWO NUMBERS AGREE: lcoh_yaml_only (via _lcoh_for()) and
    potential/h2_potential.py (which sized every site's own
    annual_h2_production_t -- neither re-run nor modified here, per Hard
    Rule 7) both resolve full_load_hours through the SAME
    _renewable_capacity_factor(config, region, renewable_tech, scenario),
    which reads config.technologies.<renewable_tech>.<region>.capacity_factor
    (a literature-validated {min, baseline, max} range) at the SAME
    `scenario` branch this function and h2_potential.py's own caller both
    use -- see docs/memory/09_methodology_assumptions.md. h2_potential.py
    applies that one resolved capacity factor uniformly
    across all of a region+tech's sites (not a per-site raster-derived
    one -- see its own PARAMETER NOTE), so every site's
    hydrogen_output_kg_per_kw_year (annual_h2_production_t /
    installable_capacity_mw; tonnes/MW and kg/kW are the same ratio)
    reduces algebraically to the same value _lcoh_for() computes directly
    from config. A close match is therefore the expected, healthy result
    confirming the two code paths agree; a real divergence would mean
    they've drifted out of sync again and need investigating.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind" -- site_selection.py/h2_potential.py's siting-
        technology vocabulary (mapped internally to this module's
        "solar_pv"/"onshore_wind" renewable_tech for WACC/LCOE lookups).
    config : ScenarioConfig

    Returns
    -------
    dict
        region, tech, renewable_tech, n_sites,
        lcoh_per_site : list[dict], one per candidate site --
            {site_id, installable_capacity_mw, annual_h2_tons,
            suitable_area_km2, hydrogen_output_kg_per_kw_year,
            lcoh_usd_per_kg}
        lcoh_aggregated : float | None -- area-weighted mean LCOH across
            all candidate sites (None if there are zero candidate sites,
            or all have non-positive installable capacity)
        lcoh_yaml_only : float -- decompose_actual()'s pure-YAML LCOH for
            the same region/renewable_tech

    Raises
    ------
    ValueError
        If `tech` is not "solar" or "wind".
    FileNotFoundError
        If h2_potential_{region}_{tech}_{scenario}.geojson has not been
        written yet -- run
        potential.h2_potential.calculate_h2_potential(region, tech, config,
        scenario=scenario) first.
    """
    if tech not in _TECH_TO_RENEWABLE_TECH:
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")
    renewable_tech = _TECH_TO_RENEWABLE_TECH[tech]

    sites_path = H2_POTENTIAL_DIR / f"h2_potential_{region.value}_{tech}_{scenario}.geojson"
    if not sites_path.exists():
        raise FileNotFoundError(
            f"h2_potential output not found: {sites_path}. Run "
            f"src.potential.h2_potential.calculate_h2_potential({region.value!r}, "
            f"{tech!r}, config, scenario={scenario!r}) first."
        )
    sites = gpd.read_file(sites_path)

    lcoh_yaml_only = _lcoh_for(region, config, renewable_tech=renewable_tech, scenario=scenario)

    region_cfg = _region_config(region, config)
    renewable_cfg = getattr(config.renewables, renewable_tech)
    base = _electrolyzer_defaults(config)
    wacc = _tech_wacc(region_cfg, renewable_tech, scenario=scenario)
    # Electrolyzer full-load hours: bounded by the renewable's own capacity
    # factor (see _lcoh_for()'s FIXED BUG note and _renewable_capacity_factor()),
    # NOT 8760 (always-on) -- a dedicated off-take can't run when its only
    # power source isn't producing. h2_potential.py resolves the SAME
    # config.technologies.<tech>.<region>.capacity_factor via this same
    # decomposition._renewable_capacity_factor() helper (imported, not
    # reimplemented), at the SAME `scenario` branch, so this aggregated
    # LCOH and h2_potential.py's own per-site sizing can never silently
    # diverge onto two different capacity factors for the same
    # region+tech+scenario.
    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech, scenario=scenario)
    renewable_capex = _effective_renewable_capex_for_scenario(
        region_cfg, renewable_cfg, renewable_tech, scenario
    )
    lcoe_usd_per_kwh = _renewable_lcoe(
        config, region_cfg, renewable_tech, wacc, capacity_factor,
        renewable_capex_override=renewable_capex,
    )
    full_load_hours = _renewable_full_load_hours(config, region, renewable_tech, scenario=scenario)

    lcoh_per_site = []
    for _, row in sites.iterrows():
        capacity_mw = float(row["installable_capacity_mw"])
        annual_h2_t = float(row["annual_h2_production_t"])
        area_km2 = float(row["suitable_area_km2"])
        if capacity_mw <= 0:
            continue  # cannot derive a per-kW output ratio from zero capacity

        # tonnes/yr per MW == kg/yr per kW (both scale by exactly 1000).
        hydrogen_output_kg_per_kw_year = annual_h2_t / capacity_mw

        site_lcoh = calculate_lcoh(
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
            incentive_value_usd_per_kg=0.0,
            incentive_duration_years=0.0,
        )
        lcoh_per_site.append({
            "site_id": row["site_id"],
            "installable_capacity_mw": capacity_mw,
            "annual_h2_tons": annual_h2_t,
            "suitable_area_km2": area_km2,
            "hydrogen_output_kg_per_kw_year": hydrogen_output_kg_per_kw_year,
            "lcoh_usd_per_kg": site_lcoh,
        })

    total_area_km2 = sum(s["suitable_area_km2"] for s in lcoh_per_site)
    lcoh_aggregated = (
        sum(s["lcoh_usd_per_kg"] * s["suitable_area_km2"] for s in lcoh_per_site) / total_area_km2
        if total_area_km2 > 0 else None
    )

    if lcoh_aggregated is not None:
        print(
            f"[decomposition] actual_per_region_aggregated | {region.value}/{tech} "
            f"({renewable_tech}): {len(lcoh_per_site)} site(s), LCOH_aggregated = "
            f"{lcoh_aggregated:.3f} USD/kg vs LCOH_yaml_only = {lcoh_yaml_only:.3f} "
            f"USD/kg (diff = {lcoh_aggregated - lcoh_yaml_only:+.4f})"
        )
    else:
        print(
            f"[decomposition] actual_per_region_aggregated | {region.value}/{tech}: "
            f"no usable candidate sites -- lcoh_aggregated is None, "
            f"LCOH_yaml_only = {lcoh_yaml_only:.3f} USD/kg"
        )

    return {
        "region": region.value,
        "tech": tech,
        "renewable_tech": renewable_tech,
        "n_sites": len(lcoh_per_site),
        "lcoh_per_site": lcoh_per_site,
        "lcoh_aggregated": lcoh_aggregated,
        "lcoh_yaml_only": lcoh_yaml_only,
    }


def decompose_wacc_swap(
    region_a: Region, region_b: Region, config, renewable_tech: str = "solar_pv"
) -> dict:
    """
    Counterfactual: compute LCOH for region_a using region_b's WACC for the
    given renewable_tech (solar swaps with solar, wind swaps with wind —
    WACC is technology-specific), while keeping region_a's own CAPEX
    multiplier fixed. This isolates the effect of the financing-cost
    differential, independent of CAPEX-side differences.

    CRITICAL INVARIANT: the CAPEX multiplier NEVER travels with the WACC.
    Only `swapped_wacc` (sourced from region_b) is passed as wacc_override;
    `_lcoh_for` always resolves capex_multiplier from region_a's own config
    (region_a_cfg.capex_multiplier), regardless of which WACC is used.
    """
    region_a_cfg = _region_config(region_a, config)
    region_b_cfg = _region_config(region_b, config)
    swapped_wacc = _tech_wacc(region_b_cfg, renewable_tech)

    lcoh_value = _lcoh_for(
        region_a, config, renewable_tech=renewable_tech, wacc_override=swapped_wacc
    )

    print(
        f"[decomposition] wacc_swap | {region_a.value} using "
        f"{region_b.value}'s {renewable_tech} WACC: LCOH = {lcoh_value:.3f} USD/kg"
    )

    return {
        "run_type": "wacc_swap",
        "region": region_a.value,
        "renewable_tech": renewable_tech,
        "wacc_source_region": region_b.value,
        "wacc_used": swapped_wacc,
        "capex_multiplier_used": region_a_cfg.capex_multiplier,
        "lcoh_usd_per_kg": lcoh_value,
    }


def run_all_decompositions(config) -> None:
    """
    Orchestrates the decomposition experiments for the Brazil/Germany pair
    and writes results to outputs/tables/. Creates the output directory if
    it does not exist.

    No scenario.active anywhere -- the pipeline always executes all three
    combined scenarios (min/baseline/max) via run_scenario_range(), which
    is where decomposition.csv's scenario-dependent rows and range summary
    come from. decompose_actual()/decompose_wacc_swap() remain baseline-only
    point estimates (their existing purpose: MC-uncertainty-quantified
    actual LCOH and the WACC-swap counterfactual). The 1D inversion-point
    search is gone (see ADR-009, 06_technical_decisions_log.md) -- the 2D
    WACC_BR x WACC_DE competitiveness frontier that replaces it is
    orchestrated separately, once, from run_pipeline.py's stage_economics()
    (src/economics/competitiveness_frontier.py), not from this function.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    assumption_rows = []
    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        for renewable_tech in ("solar_pv", "onshore_wind"):
            assumption_rows.append(get_input_assumptions(region, renewable_tech, config))
    assumptions_df = pd.concat(assumption_rows, ignore_index=True)
    assumptions_df.to_csv(INPUT_ASSUMPTIONS_CSV, index=False)
    print(f"[decomposition] wrote {INPUT_ASSUMPTIONS_CSV} ({len(assumptions_df)} rows)")

    rows = []
    for renewable_tech in ("solar_pv", "onshore_wind"):
        rows.append(decompose_actual(Region.NORDESTE_BR, config, renewable_tech))
        rows.append(decompose_actual(Region.NORTH_GERMANY, config, renewable_tech))
        rows.append(
            decompose_wacc_swap(
                Region.NORDESTE_BR, Region.NORTH_GERMANY, config, renewable_tech
            )
        )
        rows.append(
            decompose_wacc_swap(
                Region.NORTH_GERMANY, Region.NORDESTE_BR, config, renewable_tech
            )
        )
        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            rows.extend(run_scenario_range(region, config, renewable_tech)["rows"])

    pd.DataFrame(rows).to_csv(DECOMPOSITION_CSV, index=False)
    print(f"[decomposition] wrote {DECOMPOSITION_CSV}")


if __name__ == "__main__":
    from config.config_loader import load_scenario_config

    cfg = load_scenario_config()
    run_all_decompositions(cfg)