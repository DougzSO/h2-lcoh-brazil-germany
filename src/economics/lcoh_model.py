"""
src/economics/lcoh_model.py

Pure financial function implementing the discounted lifetime LCOH
(Levelized Cost of Hydrogen) formula.

Electricity cost is included via a REQUIRED `lcoe_usd_per_kwh` argument
(no default): the discounted cost of electricity consumed by the
electrolyzer (full_load_hours kWh per kW of capacity per year, valued at
the renewable LCOE) is now part of the cost stack. This closes the gap
that previously allowed LCOH to go negative whenever a production
incentive exceeded electrolyzer-only costs.

This module has NO project imports (per spec) — only the standard library.
"""

from __future__ import annotations


def calculate_lcoh(
    capex_usd_per_kw: float,
    opex_pct_of_capex: float,
    wacc: float,
    lifetime_years: int,
    hydrogen_output_kg_per_kw_year: float,
    stack_replacement_pct_of_capex: float,
    stack_lifetime_hours: float,
    full_load_hours: float,
    water_cost_usd_per_m3: float,
    water_consumption_l_per_kg: float,
    lcoe_usd_per_kwh: float,
    capex_multiplier: float = 1.0,
    incentive_value_usd_per_kg: float = 0.0,
    incentive_duration_years: float = 0.0,
) -> float:
    """
    Compute the discounted lifetime Levelized Cost of Hydrogen (LCOH).

    Formula
    -------
    LCOH = ( CAPEX_total
             + sum_t [ OPEX_t / (1+wacc)^t ]
             + sum_t [ StackReplacement_t / (1+wacc)^t ]
             + sum_t [ WaterCost_t / (1+wacc)^t ]
             + sum_t [ EnergyCost_t / (1+wacc)^t ]
             - sum_t [ Incentive_t / (1+wacc)^t ]
           )
           / sum_t [ H2Production_t / (1+wacc)^t ]

    All sums run over t = 1 .. lifetime_years (end-of-year discounting).

    Units
    -----
    - capex_usd_per_kw : USD / kW of electrolyzer capacity (undiscounted, year 0)
    - opex_pct_of_capex : fraction of CAPEX_total spent per year on O&M
    - wacc : discount rate, e.g. 0.08 for 8%
    - lifetime_years : project economic life, in years
    - hydrogen_output_kg_per_kw_year : kg H2 produced per kW of capacity per year
    - stack_replacement_pct_of_capex : fraction of CAPEX_total spent each time
      the stack is replaced
    - stack_lifetime_hours : operating hours before a stack replacement is needed
    - full_load_hours : equivalent full-load operating hours per year
      (capacity_factor * 8760), used to convert stack_lifetime_hours into a
      replacement interval in years
    - water_cost_usd_per_m3 : USD per cubic meter of process water
    - water_consumption_l_per_kg : liters of water consumed per kg of H2
    - capex_multiplier : region/scenario CAPEX scaling factor (e.g. 1.15 for
      Brazil logistics premium)
    - incentive_value_usd_per_kg : production tax credit, USD per kg H2
    - incentive_duration_years : number of years (from year 1) the incentive applies
    - lcoe_usd_per_kwh : REQUIRED levelized cost of renewable electricity fed
      to the electrolyzer, USD per kWh. Annual electricity consumption is
      derived as full_load_hours * 1 kW (i.e. full_load_hours kWh per kW of
      electrolyzer capacity per year), so annual energy cost is
      full_load_hours * lcoe_usd_per_kwh.

    Returns
    -------
    float
        LCOH in USD per kg of hydrogen.

    Raises
    ------
    ValueError
        If lifetime_years <= 0, full_load_hours <= 0, or wacc <= -1.0
        (which would make discounting undefined/degenerate).
    """
    if lifetime_years <= 0:
        raise ValueError(f"lifetime_years must be > 0, got {lifetime_years}")
    if full_load_hours <= 0:
        raise ValueError(f"full_load_hours must be > 0, got {full_load_hours}")
    if wacc <= -1.0:
        raise ValueError(f"wacc must be > -1.0, got {wacc}")

    # --- Base CAPEX, scaled by region/scenario multiplier ------------------
    capex_total = capex_usd_per_kw * capex_multiplier  # USD/kW, undiscounted

    # --- Annual OPEX (constant, as % of total CAPEX) ------------------------
    annual_opex = opex_pct_of_capex * capex_total  # USD/kW/year

    # --- Stack replacement interval and cost --------------------------------
    # Convert stack lifetime (operating hours) into a calendar interval
    # using the equivalent full-load hours per year.
    replacement_interval_years = stack_lifetime_hours / full_load_hours
    stack_replacement_cost = stack_replacement_pct_of_capex * capex_total  # USD/kW

    # --- Annual water cost ---------------------------------------------------
    # water_consumption_l_per_kg is per kg H2; convert L -> m3 (divide by 1000)
    annual_water_cost = (
        hydrogen_output_kg_per_kw_year
        * water_consumption_l_per_kg
        / 1000.0
        * water_cost_usd_per_m3
    )  # USD/kW/year

    # --- Annual energy cost (REQUIRED) ---------------------------------------
    # Electricity consumed per kW of electrolyzer capacity per year equals
    # full_load_hours (kWh), since capacity is expressed per kW electrical
    # input. Valued at the region/technology-specific renewable LCOE.
    annual_energy_cost = full_load_hours * lcoe_usd_per_kwh  # USD/kW/year

    # --- Discounted sums over the project lifetime --------------------------
    discounted_cost = capex_total  # year-0 CAPEX is undiscounted
    discounted_production = 0.0
    discounted_incentive = 0.0

    for year in range(1, lifetime_years + 1):
        discount_factor = (1.0 + wacc) ** year

        # Recurring annual costs
        discounted_cost += (
            annual_opex + annual_water_cost + annual_energy_cost
        ) / discount_factor

        # Stack replacement: occurs at integer multiples of the replacement
        # interval, strictly before the end of project life. A replacement
        # exactly at the final year is excluded (no point replacing a stack
        # the year the plant retires).
        if replacement_interval_years > 0:
            n_replacement = round(year / replacement_interval_years)
            is_replacement_year = (
                n_replacement > 0
                and abs(year - n_replacement * replacement_interval_years) < 0.5
                and year < lifetime_years
            )
            if is_replacement_year:
                discounted_cost += stack_replacement_cost / discount_factor

        # Hydrogen production (discounted)
        discounted_production += hydrogen_output_kg_per_kw_year / discount_factor

        # Production incentive, only within its stated duration
        if year <= incentive_duration_years:
            discounted_incentive += (
                incentive_value_usd_per_kg * hydrogen_output_kg_per_kw_year
            ) / discount_factor

    if discounted_production <= 0:
        raise ValueError("Discounted hydrogen production is zero or negative")

    lcoh = (discounted_cost - discounted_incentive) / discounted_production
    return lcoh


def calculate_lcoe(
    capex_usd_per_kw: float,
    opex_pct_of_capex: float,
    wacc: float,
    lifetime_years: int,
    capacity_factor: float,
) -> float:
    """
    Compute the discounted Levelized Cost of Electricity (LCOE) for a
    renewable generation technology, to be used as the `lcoe_usd_per_kwh`
    input of `calculate_lcoh`.

    Formula
    -------
    LCOE = ( CAPEX + sum_t [ OPEX_t / (1+wacc)^t ] )
           / sum_t [ Energy_t / (1+wacc)^t ]

    where Energy_t = capacity_factor * 8760 kWh per kW of installed capacity
    per year, and sums run over t = 1 .. lifetime_years.

    Units
    -----
    - capex_usd_per_kw : USD / kW of installed renewable capacity
    - opex_pct_of_capex : fraction of CAPEX spent per year on O&M
    - wacc : discount rate specific to this technology (e.g. solar WACC or
      wind WACC — WACC is technology-specific, not a regional average)
    - lifetime_years : plant economic life, in years
    - capacity_factor : fraction of the year the plant runs at full capacity

    Returns
    -------
    float
        LCOE in USD per kWh.
    """
    if lifetime_years <= 0:
        raise ValueError(f"lifetime_years must be > 0, got {lifetime_years}")
    if not (0.0 < capacity_factor <= 1.0):
        raise ValueError(f"capacity_factor must be in (0,1], got {capacity_factor}")
    if wacc <= -1.0:
        raise ValueError(f"wacc must be > -1.0, got {wacc}")

    annual_opex = opex_pct_of_capex * capex_usd_per_kw
    annual_energy_kwh = capacity_factor * 8760.0

    discounted_cost = capex_usd_per_kw
    discounted_energy = 0.0
    for year in range(1, lifetime_years + 1):
        discount_factor = (1.0 + wacc) ** year
        discounted_cost += annual_opex / discount_factor
        discounted_energy += annual_energy_kwh / discount_factor

    if discounted_energy <= 0:
        raise ValueError("Discounted energy production is zero or negative")

    return discounted_cost / discounted_energy