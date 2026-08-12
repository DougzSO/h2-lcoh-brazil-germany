"""
src/sensitivity/sensitivity_analysis.py

Three independent sensitivity checks, per METHODOLOGY.md §2.7:

1. Economic one-at-a-time (OAT) sweep over WACC, electrolyzer CAPEX,
   electrolyzer efficiency, electrolyzer technology (PEM vs alkaline), and
   region CAPEX multiplier, for both regions and both renewable
   technologies. Unchanged from the prior session except for the output
   filename (now `economic_sensitivity.csv`, matching this session's spec).

2. Sobol global variance-based sensitivity analysis (`run_sobol_analysis`,
   below) over the SAME region x renewable_tech pairs, quantifying
   parameter INTERACTIONS the OAT sweep cannot see by construction, since
   OAT only ever varies one parameter at a time from a fixed base case.
   Uses SALib's Saltelli sampling + Sobol estimator against the literature
   min/max ranges already in scenario_params.yaml -- see
   `SOBOL_PARAMETERS`' module-level comment for exactly which parameters
   qualify and which are excluded, and why.

3. Spatial MCDA weight-perturbation vs. VIKOR concordance check: each of
   `topsis.py`'s 4 criterion weights is perturbed +/-20% in isolation
   (`topsis.perturb_topsis_weights`), the perturbed TOPSIS ranking is
   compared against both the TOPSIS baseline and the independent VIKOR
   baseline (`vikor.compute_concordance`), confirming the suitability
   result is robust both to the specific weights used within TOPSIS and to
   the choice of MCDA method itself (the same robustness claim
   METHODOLOGY.md §2.3 makes about VIKOR alone, extended here to weight
   sensitivity). NEITHER TOPSIS nor VIKOR math is re-implemented here --
   `run_topsis`, `run_vikor`, `perturb_topsis_weights`, and
   `compute_concordance` are imported and called as-is.

WACC is technology-specific (solar_pv / onshore_wind), matching
decomposition.py: the base case for each region uses that region's WACC
for the chosen renewable_tech, and the matching renewable LCOE
(lcoh_model.calculate_lcoe) is fed into calculate_lcoh's required
lcoe_usd_per_kwh argument. Base electrolyzer technology is PEM; alkaline
appears ONLY as a one-off sensitivity variant in the
"electrolyzer_technology" sweep, never as the base case.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from SALib.analyze.sobol import analyze as sobol_analyze
from SALib.sample.sobol import sample as sobol_sample

from src.core.constants import Region, RegionCRS
from src.economics.decomposition import (
    _effective_renewable_capex,
    _renewable_capacity_factor,
    _renewable_full_load_hours,
    _tech_wacc,
)
from src.economics.lcoh_model import calculate_lcoh, calculate_lcoe
from src.spatial import data_layers
from src.spatial.exclusion_mask import create_exclusion_mask
from src.spatial.grid_utils import get_analysis_grid
from src.spatial.topsis import (
    CRITERIA,
    CRITERION_DIRECTION,
    TopsisError,
    _get_default_weights,
    _load_criterion,
    _vectorized_topsis,
    perturb_topsis_weights,
)
from src.spatial.vikor import _vectorized_vikor, compute_concordance

VIKOR_V = 0.5  # compromise weight, matching vikor.run_vikor()'s own default

OUTPUT_DIR = "outputs/tables"
ECONOMIC_SENSITIVITY_CSV = os.path.join(OUTPUT_DIR, "economic_sensitivity.csv")
MCDA_CONCORDANCE_TOP_K_PCT = 0.10

_REGION_TO_CONFIG_ATTR = {
    Region.NORDESTE_BR: "brazil",
    Region.NORTH_GERMANY: "germany",
}

def _region_config(region: Region, config):
    return getattr(config.regions, _REGION_TO_CONFIG_ATTR[region])


def _capacity_factor_range(config, region: Region, renewable_tech: str) -> tuple[float, float]:
    """
    Literature-validated (min, max) band for this region+technology's
    capacity factor: config.technologies.<renewable_tech>.<region>.
    capacity_factor.{min,max} (scenario_params.yaml -- see
    docs/memory/09_methodology_assumptions.md). Every region+technology
    pair has its own explicit range in this schema, so unlike before this
    migration there is no default/fallback band to compute.
    """
    tech_cfg = getattr(config.technologies, renewable_tech)
    region_key = _REGION_TO_CONFIG_ATTR[region]
    cf_param = getattr(tech_cfg, region_key).capacity_factor
    return cf_param.min, cf_param.max


def _renewable_lcoe(config, region: Region, renewable_tech: str, wacc: float) -> float:
    """Renewable LCOE (USD/kWh) for the given technology and its own WACC,
    using THIS region+technology's own resolved capacity factor
    (economics.decomposition._renewable_capacity_factor(), reused rather
    than re-implemented -- see module docstring). CAPEX resolution
    (economics.decomposition._effective_renewable_capex()) is reused too,
    so a region-specific override (Brazil onshore_wind today) cannot
    silently diverge between this OAT/Sobol sweep and decomposition.py's
    own actual/wacc_swap/inversion-point runs."""
    renewable_cfg = getattr(config.renewables, renewable_tech)
    region_cfg = _region_config(region, config)
    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech)
    return calculate_lcoe(
        capex_usd_per_kw=_effective_renewable_capex(region_cfg, renewable_cfg, renewable_tech),
        opex_pct_of_capex=renewable_cfg.opex_pct_of_capex,
        wacc=wacc,
        lifetime_years=renewable_cfg.lifetime_years,
        capacity_factor=capacity_factor,
    )


def _base_lcoh_kwargs(
    region: Region, config, renewable_tech: str = "solar_pv", technology: str = "pem"
) -> dict:
    """
    Build the baseline calculate_lcoh keyword arguments for a region,
    using PEM electrolyzer parameters (base case — alkaline is sensitivity
    only) and the WACC tied to renewable_tech (never a regional average).
    Returned as a dict so individual keys can be overridden per sweep step
    without recomputing everything.

    FIXED BUG: capacity_factor/full_load_hours here previously read
    config.thresholds.min_capacity_factor (0.15) -- a proposed, currently
    unused-by-exclusion_mask.py binary EXCLUSION threshold, a materially
    different concept from this technology's own resolved OPERATING
    capacity factor. Every OAT sweep's "base_value" LCOH was silently
    computed at the wrong capacity factor for every region+tech pair as a
    result. Now resolved the same way decomposition.py and h2_potential.py
    do: economics.decomposition._renewable_capacity_factor()/
    _renewable_full_load_hours(), reading
    config.technologies.<renewable_tech>.<region> (see
    docs/memory/09_methodology_assumptions.md).
    """
    region_cfg = _region_config(region, config)
    tech_cfg = config.electrolyzer.technologies[technology]
    shared_cfg = config.electrolyzer.shared

    wacc = _tech_wacc(region_cfg, renewable_tech)
    lcoe_usd_per_kwh = _renewable_lcoe(config, region, renewable_tech, wacc)

    full_load_hours = _renewable_full_load_hours(config, region, renewable_tech)
    hydrogen_output_kg_per_kw_year = full_load_hours / tech_cfg.efficiency_kwh_per_kg

    incentives = region_cfg.incentives
    incentive_value = incentives.get("production_credit_usd_per_kg", 0.0)
    incentive_duration = incentives.get("support_period_years", 0.0)

    return {
        "capex_usd_per_kw": tech_cfg.capex_usd_per_kw,
        "opex_pct_of_capex": shared_cfg.opex_pct_of_capex,
        "wacc": wacc,
        "lifetime_years": shared_cfg.lifetime_years,
        "hydrogen_output_kg_per_kw_year": hydrogen_output_kg_per_kw_year,
        "stack_replacement_pct_of_capex": tech_cfg.stack_replacement_pct_of_capex,
        "stack_lifetime_hours": tech_cfg.stack_lifetime_hours,
        "full_load_hours": full_load_hours,
        "water_cost_usd_per_m3": shared_cfg.water_cost_usd_per_m3,
        "water_consumption_l_per_kg": shared_cfg.water_consumption_l_per_kg,
        "lcoe_usd_per_kwh": lcoe_usd_per_kwh,
        "capex_multiplier": region_cfg.capex_multiplier,
        "incentive_value_usd_per_kg": incentive_value,
        "incentive_duration_years": incentive_duration,
    }


_TECH_TO_WACC_KEY = {"solar_pv": "solar", "onshore_wind": "wind"}


def _wacc_sweep_values(region_cfg, renewable_tech: str, steps: int) -> np.ndarray:
    """Linearly spaced WACC values across THIS technology's own
    wacc.<solar|wind> {min, max} RangeParam bounds -- technology-specific,
    not a shared region-wide range (WACC was refactored from a scalar +
    shared `range` tuple into a per-technology RangeParam; solar and wind
    each have their own min/max band now, previously identical for both)."""
    param = getattr(region_cfg.wacc, _TECH_TO_WACC_KEY[renewable_tech])
    return np.linspace(param.min, param.max, steps)


def _capex_multiplier_sweep_values(
    capex_multiplier_range, steps: int
) -> np.ndarray:
    low, high = capex_multiplier_range
    return np.linspace(low, high, steps)


def run_sensitivity(config) -> pd.DataFrame:
    """
    Run a one-at-a-time sensitivity sweep for both regions across:
      - wacc (this region+technology's own wacc.<solar|wind> {min, max}
        RangeParam bounds, config.sensitivity.wacc_steps points)
      - electrolyzer capex_usd_per_kw (+/- 20% of base, same step count as capex sweep)
      - electrolyzer efficiency_kwh_per_kg (+/- 20% of base)
      - capacity_factor (this region+tech's own literature {min, max} band,
        config.technologies.<renewable_tech>.<region>.capacity_factor --
        see _capacity_factor_range())
      - region capex_multiplier (config.sensitivity.capex_multiplier_range)

    Returns
    -------
    pandas.DataFrame
        Columns: region, parameter, base_value, varied_value, lcoh_result
    """
    rows: List[dict] = []
    wacc_steps = config.sensitivity.wacc_steps
    capex_mult_steps = config.sensitivity.capex_multiplier_steps
    capex_mult_range = config.sensitivity.capex_multiplier_range

    for renewable_tech in ("solar_pv", "onshore_wind"):
        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            region_cfg = _region_config(region, config)
            base_kwargs = _base_lcoh_kwargs(
                region, config, renewable_tech=renewable_tech
            )

            print(
                f"[sensitivity] running sweeps for {region.value} / {renewable_tech}"
            )

            # --- WACC sweep (technology-specific range) -----------------
            base_wacc = base_kwargs["wacc"]
            for wacc_value in _wacc_sweep_values(region_cfg, renewable_tech, wacc_steps):
                kwargs = dict(base_kwargs, wacc=float(wacc_value))
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "wacc",
                        "base_value": base_wacc,
                        "varied_value": float(wacc_value),
                        "lcoh_result": lcoh_value,
                    }
                )

            # --- Electrolyzer CAPEX sweep (+/- 20% of base, same step
            # count as the capex_multiplier sweep, per config.sensitivity)
            base_capex = base_kwargs["capex_usd_per_kw"]
            for capex_value in np.linspace(
                base_capex * 0.8, base_capex * 1.2, capex_mult_steps
            ):
                kwargs = dict(base_kwargs, capex_usd_per_kw=float(capex_value))
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "electrolyzer_capex_usd_per_kw",
                        "base_value": base_capex,
                        "varied_value": float(capex_value),
                        "lcoh_result": lcoh_value,
                    }
                )

            # --- Electrolyzer efficiency sweep (+/- 20% of base) --------
            # NOTE: efficiency also determines hydrogen_output_kg_per_kw_year
            # (output = full_load_hours / efficiency), so both must be
            # recomputed together, or the sweep silently only affects one
            # side of the LCOH ratio.
            base_efficiency = config.electrolyzer.technologies[
                "pem"
            ].efficiency_kwh_per_kg
            full_load_hours = base_kwargs["full_load_hours"]
            for efficiency_value in np.linspace(
                base_efficiency * 0.8, base_efficiency * 1.2, capex_mult_steps
            ):
                hydrogen_output = full_load_hours / efficiency_value
                kwargs = dict(
                    base_kwargs,
                    hydrogen_output_kg_per_kw_year=float(hydrogen_output),
                )
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "electrolyzer_efficiency_kwh_per_kg",
                        "base_value": base_efficiency,
                        "varied_value": float(efficiency_value),
                        "lcoh_result": lcoh_value,
                    }
                )

            # --- Capacity factor sweep (this region+tech's own literature
            # {min, max} band, config.technologies.<renewable_tech>.
            # <region>.capacity_factor -- see _capacity_factor_range()).
            # Sweeping this varies BOTH full_load_hours and
            # hydrogen_output_kg_per_kw_year together, same as the
            # efficiency sweep above, since both derive from the same
            # physical capacity factor (see
            # economics/decomposition.py's _renewable_capacity_factor()).
            base_capacity_factor = base_kwargs["full_load_hours"] / 8760.0
            pem_efficiency = config.electrolyzer.technologies["pem"].efficiency_kwh_per_kg
            cf_low, cf_high = _capacity_factor_range(config, region, renewable_tech)
            for cf_value in np.linspace(cf_low, cf_high, capex_mult_steps):
                flh_value = float(cf_value) * 8760.0
                hydrogen_output = flh_value / pem_efficiency
                kwargs = dict(
                    base_kwargs,
                    full_load_hours=flh_value,
                    hydrogen_output_kg_per_kw_year=float(hydrogen_output),
                )
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "capacity_factor",
                        "base_value": base_capacity_factor,
                        "varied_value": float(cf_value),
                        "lcoh_result": lcoh_value,
                    }
                )

            # --- Electrolyzer technology sweep: PEM (base) vs alkaline ---
            # PEM is the base case everywhere else in this module; alkaline
            # appears ONLY here, as a discrete sensitivity variant, never
            # as a substitute base case.
            for alt_technology in ("pem", "alkaline"):
                alt_tech_cfg = config.electrolyzer.technologies[alt_technology]
                alt_full_load_hours = base_kwargs["full_load_hours"]
                alt_hydrogen_output = (
                    alt_full_load_hours / alt_tech_cfg.efficiency_kwh_per_kg
                )
                kwargs = dict(
                    base_kwargs,
                    capex_usd_per_kw=alt_tech_cfg.capex_usd_per_kw,
                    stack_replacement_pct_of_capex=alt_tech_cfg.stack_replacement_pct_of_capex,
                    stack_lifetime_hours=alt_tech_cfg.stack_lifetime_hours,
                    hydrogen_output_kg_per_kw_year=alt_hydrogen_output,
                )
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "electrolyzer_technology",
                        "base_value": "pem",
                        "varied_value": alt_technology,
                        "lcoh_result": lcoh_value,
                    }
                )

            # --- Region CAPEX multiplier sweep (always applied) ----------
            base_multiplier = base_kwargs["capex_multiplier"]
            for multiplier_value in _capex_multiplier_sweep_values(
                capex_mult_range, capex_mult_steps
            ):
                kwargs = dict(base_kwargs, capex_multiplier=float(multiplier_value))
                lcoh_value = calculate_lcoh(**kwargs)
                rows.append(
                    {
                        "region": region.value,
                        "renewable_tech": renewable_tech,
                        "parameter": "capex_multiplier",
                        "base_value": base_multiplier,
                        "varied_value": float(multiplier_value),
                        "lcoh_result": lcoh_value,
                    }
                )

    result_df = pd.DataFrame(rows)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result_df.to_csv(ECONOMIC_SENSITIVITY_CSV, index=False)
    print(f"[sensitivity] wrote {ECONOMIC_SENSITIVITY_CSV} ({len(result_df)} rows)")

    return result_df



# ============================================================================
# Sobol global sensitivity analysis (variance-based, quantifies parameter
# INTERACTIONS -- something run_sensitivity()'s OAT sweep above, by
# construction, cannot see, since it varies exactly one parameter at a time
# from a fixed base case). Uses SALib's Saltelli sampling + Sobol estimator.
#
# NOTE on SALib API: this task's own instructions named `saltelli.sample`
# explicitly; `SALib.sample.saltelli` (installed version 1.5.2) raises a
# DeprecationWarning at import/call time ("will be removed ... use
# salib.sample.sobol"), verified live in this environment. `SALib.sample.
# sobol.sample` is the same Saltelli sequence generator under SALib's
# current name -- used here instead to avoid a deprecation warning on every
# run, with no behavioral difference.
# ============================================================================

SOBOL_BASE_SAMPLES = 1024  # Saltelli "N" -- total evaluations = N * (num_vars + 2) with calc_second_order=False
SOBOL_CONFIDENCE_LEVEL = 0.95
SOBOL_SEED = 42
SOBOL_CSV_TEMPLATE = os.path.join(OUTPUT_DIR, "sobol_{region}_{tech}.csv")

# This task's own instructions listed 5 candidate parameters (CAPEX, OPEX,
# WACC, capacity_factor, capacity_density) but also gave the qualifying
# rule that governs which are actually usable: "for every parameter that is
# already a range dict ... read min/max directly. If any parameter is
# still scalar, raise an error -- all must be migrated to dicts first."
# Checked directly against config_loader.py's Pydantic schema (never
# assumed):
#   - wacc                -> RegionConfig.wacc.<solar|wind> (RangeParam,
#     technology-specific -- see _sobol_bounds())                                -- HAS a range
#   - capacity_factor      -> TechRegionParams.capacity_factor (RangeParam)       -- HAS a range
#   - renewable CAPEX      -> RenewableTechConfig.capex_range (Tuple[float,float]) -- HAS a range;
#     this is the CAPEX that actually reaches calculate_lcoh(), via
#     calculate_lcoe() -> the required lcoe_usd_per_kwh argument
#     (economics/decomposition.py: _renewable_lcoe())
#   - electrolyzer CAPEX/OPEX -> ElectrolyzerTechConfig.capex_usd_per_kw /
#     ElectrolyzerSharedConfig.opex_pct_of_capex are still bare floats --
#     NO min/max anywhere in scenario_params.yaml. EXCLUDED here rather
#     than assigning an invented +/-% band (explicitly forbidden by this
#     task's own "do not hardcode ... arbitrary percentage" constraint).
#   - capacity_density_mw_per_km2 -> EXCLUDED for a different, structural
#     reason: `calculate_lcoh()`'s signature (src/economics/lcoh_model.py)
#     has no capacity/area/density parameter at all. LCOH is a per-kW cost
#     ratio, mathematically independent of installed capacity -- capacity
#     density only changes h2_potential.py's installable_capacity_mw /
#     annual_h2_production_t, a materially different output this task's
#     own Sobol target (calculate_lcoh) cannot see by construction. A
#     "CAPEX x density" interaction ON LCOH is therefore undefined, not
#     merely small -- reported explicitly in run_sobol_analysis()'s banner
#     print rather than silently omitted or forced to a nonzero value.
SOBOL_PARAMETERS = ("wacc", "capacity_factor", "renewable_capex_usd_per_kw")


def _sobol_bounds(config, region: Region, renewable_tech: str) -> Dict[str, Tuple[float, float]]:
    """Literature (min, max) bounds for the 3 Sobol parameters, read
    directly from the ranges already present in scenario_params.yaml --
    never a hardcoded +/-% (see SOBOL_PARAMETERS' module-level note).

    FIXED BUG: the WACC bound previously read region_cfg.wacc.range, a
    single shared tuple used regardless of `renewable_tech` -- so a Sobol
    solar_pv sweep and a Sobol onshore_wind sweep of the SAME region
    silently sampled WACC from the identical bound, even though solar and
    wind carry materially different WACC ranges (WaccConfig was refactored
    from scalar+shared-range into a per-technology RangeParam). Now reads
    region_cfg.wacc.<solar|wind> directly, matching `renewable_tech`."""
    region_cfg = _region_config(region, config)
    wacc_param = getattr(region_cfg.wacc, _TECH_TO_WACC_KEY[renewable_tech])
    cf_low, cf_high = _capacity_factor_range(config, region, renewable_tech)
    renewable_cfg = getattr(config.renewables, renewable_tech)
    return {
        "wacc": (wacc_param.min, wacc_param.max),
        "capacity_factor": (cf_low, cf_high),
        "renewable_capex_usd_per_kw": tuple(renewable_cfg.capex_range),
    }


def _sobol_lcoh(
    config,
    renewable_tech: str,
    base_kwargs: dict,
    wacc: float,
    capacity_factor: float,
    renewable_capex: float,
) -> float:
    """
    LCOH for one Sobol parameter draw. Reuses _base_lcoh_kwargs()'s fixed
    electrolyzer/incentive fields as-is (the same convention run_sensitivity's
    OAT sweeps already use) and overrides exactly the 3 fields that respond
    to this draw's wacc/capacity_factor/renewable_capex: full_load_hours,
    hydrogen_output_kg_per_kw_year, and lcoe_usd_per_kwh.

    Deliberately UNLIKE the OAT sweep above (which holds lcoe_usd_per_kwh
    fixed at its baseline value even while sweeping wacc or capacity_factor
    in isolation -- a fine simplification for a one-at-a-time sweep):
    lcoe_usd_per_kwh is recomputed here from THIS draw's own wacc/
    capacity_factor/renewable_capex via calculate_lcoe() (the same function
    decomposition.py's _renewable_lcoe() calls). Holding it fixed would
    silently truncate wacc's and capacity_factor's true variance
    contribution, since both genuinely drive the renewable LCOE embedded in
    every LCOH figure -- Sobol's whole point is to measure that properly.
    This does not touch calculate_lcoh()/calculate_lcoe() themselves, only
    which already-existing function computes one intermediate input.
    """
    renewable_cfg = getattr(config.renewables, renewable_tech)
    lcoe = calculate_lcoe(
        capex_usd_per_kw=renewable_capex,
        opex_pct_of_capex=renewable_cfg.opex_pct_of_capex,
        wacc=wacc,
        lifetime_years=renewable_cfg.lifetime_years,
        capacity_factor=capacity_factor,
    )
    full_load_hours = capacity_factor * 8760.0
    pem_efficiency = config.electrolyzer.technologies["pem"].efficiency_kwh_per_kg
    kwargs = dict(
        base_kwargs,
        wacc=wacc,
        full_load_hours=full_load_hours,
        hydrogen_output_kg_per_kw_year=full_load_hours / pem_efficiency,
        lcoe_usd_per_kwh=lcoe,
    )
    return calculate_lcoh(**kwargs)


def run_sobol_analysis(config, n_samples: int = SOBOL_BASE_SAMPLES) -> Dict[str, pd.DataFrame]:
    """
    Sobol global variance-based sensitivity analysis for LCOH, for both
    regions and both renewable technologies. Saltelli-sampled parameter
    combinations are each evaluated through the unmodified calculate_lcoh()
    (see _sobol_lcoh()); SALib's sobol.analyze() then decomposes the
    resulting LCOH variance into each parameter's first-order effect (S1,
    its influence alone) and total effect (ST, its influence alone plus
    every interaction it participates in). ST - S1 > 0 signals a real
    interaction effect the OAT sweep in run_sensitivity() cannot detect by
    construction (OAT holds every other parameter fixed while it varies
    one).

    Parameter space: see SOBOL_PARAMETERS' module-level comment for exactly
    which of this task's originally-requested 5 candidates (CAPEX, OPEX,
    WACC, capacity_factor, capacity_density) are backed by a literature
    min/max range in scenario_params.yaml today, and why capacity_density
    cannot appear in a Sobol analysis of LCOH specifically.

    Returns
    -------
    Dict[str, pd.DataFrame]
        {"<region>_<renewable_tech>": DataFrame}, columns: parameter, S1,
        S1_conf, ST, ST_conf, interaction (= ST - S1). Also written to
        outputs/tables/sobol_{region}_{renewable_tech}.csv.
    """
    print(
        "[sensitivity] Sobol parameter space: wacc (region wacc.<solar|wind> "
        "{min,max}, technology-specific), "
        "capacity_factor (technologies.<tech>.<region>.capacity_factor "
        "{min,max}), renewable_capex_usd_per_kw (renewables.<tech>."
        "capex_range) -- all 3 already literature-validated ranges in "
        "scenario_params.yaml. Excluded: electrolyzer CAPEX/OPEX (still "
        "bare scalars in scenario_params.yaml -- no literature range to "
        "read), capacity_density (calculate_lcoh has no capacity/density "
        "parameter at all -- see SOBOL_PARAMETERS module comment)."
    )

    results: Dict[str, pd.DataFrame] = {}
    for renewable_tech in ("solar_pv", "onshore_wind"):
        for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
            base_kwargs = _base_lcoh_kwargs(region, config, renewable_tech=renewable_tech)
            bounds = _sobol_bounds(config, region, renewable_tech)

            problem = {
                "num_vars": len(SOBOL_PARAMETERS),
                "names": list(SOBOL_PARAMETERS),
                "bounds": [list(bounds[name]) for name in SOBOL_PARAMETERS],
            }
            param_values = sobol_sample(
                problem, n_samples, calc_second_order=False, seed=SOBOL_SEED
            )
            Y = np.array(
                [_sobol_lcoh(config, renewable_tech, base_kwargs, *row) for row in param_values]
            )
            Si = sobol_analyze(
                problem,
                Y,
                calc_second_order=False,
                conf_level=SOBOL_CONFIDENCE_LEVEL,
                seed=SOBOL_SEED,
            )

            df = pd.DataFrame(
                {
                    "parameter": problem["names"],
                    "S1": Si["S1"],
                    "S1_conf": Si["S1_conf"],
                    "ST": Si["ST"],
                    "ST_conf": Si["ST_conf"],
                }
            )
            df["interaction"] = df["ST"] - df["S1"]
            df = df.sort_values("ST", ascending=False).reset_index(drop=True)

            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = SOBOL_CSV_TEMPLATE.format(region=region.value, tech=renewable_tech)
            df.to_csv(out_path, index=False)
            results[f"{region.value}_{renewable_tech}"] = df

            for _, row in df.iterrows():
                print(
                    f"[sensitivity] {region.value}/{renewable_tech} Sobol: "
                    f"{row['parameter']} S1={row['S1']:.3f} ST={row['ST']:.3f} "
                    f"interaction={row['interaction']:.3f}"
                )
            print(
                f"[sensitivity] {region.value}/{renewable_tech} Sobol: "
                f"sum(S1)={df['S1'].sum():.3f} (ideal ~1.0 for an additive "
                f"model), {n_samples * (len(SOBOL_PARAMETERS) + 2)} "
                f"evaluations -> {out_path}"
            )

    return results


@contextlib.contextmanager
def _suppress_stdout():
    """Redirect stdout to an in-memory buffer, suppressing print() noise
    from create_exclusion_mask()/data_layers loaders/topsis.py/vikor.py
    during run_mcda_sensitivity()'s 9 in-memory evaluations -- none of
    them accept a logger or verbosity flag, so redirection is the only
    way to quiet them without editing every sub-module."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_stdout


def _load_decision_matrix(
    region: Region, tech: str, config
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Load the exclusion mask and all 4 TOPSIS criteria for (region, tech)
    ONCE, so run_mcda_sensitivity()'s 9 evaluations (1 baseline + 8 weight
    perturbations) can each re-run TOPSIS entirely in memory afterward --
    topsis.run_topsis() re-reads every criterion from disk and re-runs
    rasterio.reproject() on every call, which is what made the
    unoptimized 8-perturbation loop take >10 minutes.

    Returns
    -------
    (matrix, valid, mask) : Tuple[np.ndarray, np.ndarray, np.ndarray]
        matrix : (n_valid, len(topsis.CRITERIA)) decision matrix, column
            order matches topsis.CRITERIA -- pass directly to
            topsis._vectorized_topsis(matrix, weights_arr, directions).
        valid : bool array, shape = get_analysis_grid()'s out_shape --
            marks which full-grid cells matrix's rows came from, so a
            per-perturbation score vector can be scattered back into a
            full-grid array the same way topsis.run_topsis() does.
        mask : the raw exclusion mask (uint8, same shape as valid) --
            returned alongside matrix/valid (rather than a separate
            "resource_array", which per-perturbation re-scoring never
            actually needs -- only the weights change, not the criteria
            values) because vikor.compute_concordance() requires it too,
            and loading it a second time would defeat the point of this
            function.
    """
    target_crs = RegionCRS.projected_crs_for(region)
    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape

    mask, _ = create_exclusion_mask(region, config)
    resource_loader = (
        data_layers.load_solar_irradiance if tech == "solar" else data_layers.load_wind_power_density
    )
    criteria_arrays = {
        "resource": _load_criterion(resource_loader, region, config, target_crs, out_shape, transform, "resource"),
        "slope": _load_criterion(data_layers.load_slope, region, config, target_crs, out_shape, transform, "slope"),
        "distance_to_grid": _load_criterion(
            data_layers.load_distance_to_grid, region, config, target_crs, out_shape, transform, "distance_to_grid"
        ),
        "distance_to_water": _load_criterion(
            data_layers.load_distance_to_water, region, config, target_crs, out_shape, transform, "distance_to_water"
        ),
    }

    valid = mask == 1
    for arr in criteria_arrays.values():
        valid &= np.isfinite(arr)
    if not valid.any():
        raise TopsisError(
            f"No valid unmasked cells for {region.value!r}/{tech!r} -- exclusion mask and "
            f"criteria rasters do not overlap. Check upstream data before proceeding."
        )

    matrix = np.column_stack([criteria_arrays[c][valid] for c in CRITERIA])
    return matrix, valid, mask


def run_mcda_sensitivity(
    region: Region, tech: str, config, delta_pct: float = 0.20
) -> pd.DataFrame:
    """
    Weight-perturbation vs. VIKOR concordance check for one region/tech
    pair (METHODOLOGY.md §2.7): each of TOPSIS's 4 criterion weights
    (`topsis.CRITERIA`) is perturbed by +/-delta_pct in isolation
    (`topsis.perturb_topsis_weights`, which proportionately renormalizes
    the rest), the perturbed TOPSIS ranking is re-run
    (`topsis.run_topsis(..., custom_weights=...)`), and compared via
    `vikor.compute_concordance` against both the unperturbed TOPSIS
    baseline and the independent VIKOR baseline.

    Neither TOPSIS nor VIKOR math is re-implemented here -- perturb_topsis_weights,
    compute_concordance, and topsis._vectorized_topsis (the pure-math core
    topsis.run_topsis() itself calls, with no I/O of its own) are called
    as-is, imported from topsis.py/vikor.py. The exclusion mask and all 4
    criteria are loaded ONCE via _load_decision_matrix() rather than once
    per perturbation (see that function's docstring) -- all 9 evaluations
    (baseline + 8 perturbations) re-run only the in-memory TOPSIS math.
    vikor._vectorized_vikor() (VIKOR's own no-I/O math core, mirroring
    topsis._vectorized_topsis()) computes the VIKOR baseline from the SAME
    cached matrix, rather than calling vikor.run_vikor() and reloading
    everything from disk a second time; vikor.py itself is not modified.
    All sub-module print output during
    loading/scoring is suppressed (see _suppress_stdout()); only one
    summary line is printed at the end.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind".
    config : ScenarioConfig
    delta_pct : float
        Fractional weight perturbation applied in each direction, e.g.
        0.20 for +/-20% (METHODOLOGY.md §2.7's stated value; not read from
        ScenarioConfig since it is a sensitivity-design choice, not a
        physical/economic parameter).

    Returns
    -------
    pandas.DataFrame
        One "baseline" row (TOPSIS-vs-VIKOR concordance with no
        perturbation) plus one row per (criterion, direction) pair
        (4 criteria x 2 directions = 8 rows). Columns: region, tech,
        criterion, perturbation_pct, perturbed_weights,
        spearman_rho_vs_topsis, top_10pct_overlap_vs_topsis,
        spearman_rho_vs_vikor, top_10pct_overlap_vs_vikor. Also written to
        outputs/tables/mcda_sensitivity_concordance_{region}_{tech}.csv.
    """
    t0 = time.time()

    with _suppress_stdout():
        matrix, valid, mask = _load_decision_matrix(region, tech, config)
        out_shape = valid.shape
        directions = tuple(CRITERION_DIRECTION[c] for c in CRITERIA)
        base_weights = _get_default_weights(config)

        def _score_grid(weights: Dict[str, float]) -> np.ndarray:
            weights_arr = np.array([weights[c] for c in CRITERIA])
            scores_valid = _vectorized_topsis(matrix, weights_arr, directions)
            full_scores = np.zeros(out_shape, dtype=np.float32)
            full_scores[valid] = scores_valid.astype(np.float32)
            return full_scores

        topsis_baseline = _score_grid(base_weights)

        base_weights_arr = np.array([base_weights[c] for c in CRITERIA])
        vikor_q, _, _ = _vectorized_vikor(matrix, base_weights_arr, directions, VIKOR_V)
        vikor_baseline = np.zeros(out_shape, dtype=np.float32)
        vikor_baseline[valid] = (1.0 - vikor_q).astype(np.float32)

        baseline_concordance = compute_concordance(
            topsis_baseline, vikor_baseline, mask, top_k_pct=MCDA_CONCORDANCE_TOP_K_PCT
        )

        rows: List[dict] = [
            {
                "region": region.value,
                "tech": tech,
                "criterion": "baseline",
                "perturbation_pct": 0.0,
                "perturbed_weights": dict(base_weights),
                "spearman_rho_vs_topsis": 1.0,
                "top_10pct_overlap_vs_topsis": 100.0,
                "spearman_rho_vs_vikor": baseline_concordance["spearman_rho"],
                "top_10pct_overlap_vs_vikor": baseline_concordance["top_k_overlap_pct"],
            }
        ]

        for criterion in CRITERIA:
            for direction in (delta_pct, -delta_pct):
                perturbed_weights = perturb_topsis_weights(base_weights, criterion, direction)
                topsis_perturbed = _score_grid(perturbed_weights)
                vs_topsis = compute_concordance(
                    topsis_perturbed, topsis_baseline, mask, top_k_pct=MCDA_CONCORDANCE_TOP_K_PCT
                )
                vs_vikor = compute_concordance(
                    topsis_perturbed, vikor_baseline, mask, top_k_pct=MCDA_CONCORDANCE_TOP_K_PCT
                )
                rows.append(
                    {
                        "region": region.value,
                        "tech": tech,
                        "criterion": criterion,
                        "perturbation_pct": direction,
                        "perturbed_weights": perturbed_weights,
                        "spearman_rho_vs_topsis": vs_topsis["spearman_rho"],
                        "top_10pct_overlap_vs_topsis": vs_topsis["top_k_overlap_pct"],
                        "spearman_rho_vs_vikor": vs_vikor["spearman_rho"],
                        "top_10pct_overlap_vs_vikor": vs_vikor["top_k_overlap_pct"],
                    }
                )

        result_df = pd.DataFrame(rows)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(
            OUTPUT_DIR, f"mcda_sensitivity_concordance_{region.value}_{tech}.csv"
        )
        result_df.to_csv(out_path, index=False)

    print(
        f"[sensitivity] {region.value}/{tech}: MCDA sensitivity complete "
        f"({len(result_df)} evaluations in {time.time() - t0:.1f}s)"
    )
    return result_df


def run_all_sensitivities(config) -> Dict[str, Any]:
    """
    Run the economic OAT sweep, the Sobol global sensitivity analysis, and
    the MCDA weight-perturbation vs. VIKOR concordance check, for both
    regions and both siting technologies.

    NOTE: the Sobol task's own instructions named this orchestrator
    `run_full_sensitivity()`; this project already has one top-level
    sensitivity orchestrator (`run_all_sensitivities`, wired into
    `run_pipeline.py`'s `stage_sensitivity()`), so Sobol was added as a
    third call inside it rather than creating a second, differently-named
    orchestrator that would need to be wired in separately.

    Returns
    -------
    Dict[str, Any]
        {"economic": pd.DataFrame,
         "sobol": {"<region>_<renewable_tech>": pd.DataFrame, ...},
         "mcda": {"<region>_<tech>": pd.DataFrame, ...}}
    """
    t0 = time.time()
    results: Dict[str, Any] = {}

    print("[sensitivity] === economic OAT sweep ===")
    results["economic"] = run_sensitivity(config)

    print("[sensitivity] === Sobol global sensitivity (variance-based interactions) ===")
    results["sobol"] = run_sobol_analysis(config)

    print("[sensitivity] === MCDA weight-perturbation vs VIKOR concordance ===")
    mcda_results: Dict[str, pd.DataFrame] = {}
    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        for tech in ("solar", "wind"):
            mcda_results[f"{region.value}_{tech}"] = run_mcda_sensitivity(region, tech, config)
    results["mcda"] = mcda_results

    print(f"[sensitivity] run_all_sensitivities complete in {time.time() - t0:.1f}s")
    return results


if __name__ == "__main__":
    from config.config_loader import load_scenario_config

    cfg = load_scenario_config()
    run_all_sensitivities(cfg)
