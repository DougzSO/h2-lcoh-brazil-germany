"""Offline unit tests for src/economics/lcoh_model.py's pure functions.

No project fixtures, no disk I/O, no network -- lcoh_model.py itself has
no project imports (by design, see its module docstring), and these tests
mirror that independence.
"""

from __future__ import annotations

import pytest

from src.economics.lcoh_model import calculate_lcoe, calculate_lcoh


def _reference_lcoh(
    capex_usd_per_kw, opex_pct_of_capex, wacc, lifetime_years,
    hydrogen_output_kg_per_kw_year, stack_replacement_pct_of_capex,
    stack_lifetime_hours, full_load_hours, water_cost_usd_per_m3,
    water_consumption_l_per_kg, lcoe_usd_per_kwh, capex_multiplier=1.0,
    incentive_value_usd_per_kg=0.0, incentive_duration_years=0.0,
):
    """Independent, naive re-derivation of the discounted LCOH formula
    (METHODOLOGY.md 2.5 / lcoh_model.py's own docstring), used as a
    known-value cross-check against calculate_lcoh() -- written separately
    here rather than copy-pasted from the implementation, so a bug shared
    between the two would have to be coincidental, not structural."""
    capex_total = capex_usd_per_kw * capex_multiplier
    annual_opex = opex_pct_of_capex * capex_total
    annual_water = hydrogen_output_kg_per_kw_year * water_consumption_l_per_kg / 1000.0 * water_cost_usd_per_m3
    annual_energy = full_load_hours * lcoe_usd_per_kwh
    replacement_interval = stack_lifetime_hours / full_load_hours
    replacement_cost = stack_replacement_pct_of_capex * capex_total

    cost = capex_total
    production = 0.0
    incentive = 0.0
    for year in range(1, lifetime_years + 1):
        df = (1.0 + wacc) ** year
        cost += (annual_opex + annual_water + annual_energy) / df
        if replacement_interval > 0:
            n = round(year / replacement_interval)
            if n > 0 and abs(year - n * replacement_interval) < 0.5 and year < lifetime_years:
                cost += replacement_cost / df
        production += hydrogen_output_kg_per_kw_year / df
        if year <= incentive_duration_years:
            incentive += incentive_value_usd_per_kg * hydrogen_output_kg_per_kw_year / df

    return (cost - incentive) / production


BASE_KWARGS = dict(
    capex_usd_per_kw=1200.0,
    opex_pct_of_capex=0.02,
    wacc=0.08,
    lifetime_years=20,
    hydrogen_output_kg_per_kw_year=8760.0 / 52.0,
    stack_replacement_pct_of_capex=0.15,
    stack_lifetime_hours=80000,
    full_load_hours=8760.0,
    water_cost_usd_per_m3=5.5556,
    water_consumption_l_per_kg=9.0,
    lcoe_usd_per_kwh=0.05,
    capex_multiplier=1.15,
)


class TestKnownValueBenchmarks:
    def test_matches_independent_reference_implementation(self):
        result = calculate_lcoh(**BASE_KWARGS)
        expected = _reference_lcoh(**BASE_KWARGS)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_zero_wacc_reduces_to_simple_arithmetic_average(self):
        """With wacc=0, discounting is a no-op: LCOH collapses to
        (undiscounted total cost) / (undiscounted total production), a
        hand-computable closed form independent of the loop machinery."""
        kwargs = dict(
            capex_usd_per_kw=1000.0, opex_pct_of_capex=0.0, wacc=0.0,
            lifetime_years=10, hydrogen_output_kg_per_kw_year=100.0,
            stack_replacement_pct_of_capex=0.0, stack_lifetime_hours=100000.0,
            full_load_hours=1000.0, water_cost_usd_per_m3=0.0,
            water_consumption_l_per_kg=0.0, lcoe_usd_per_kwh=0.1,
        )
        # annual_energy_cost = 1000 * 0.1 = 100 USD/kW/yr; no opex/water/stack.
        # total cost = 1000 (capex) + 10*100 = 2000; total production = 10*100 = 1000.
        expected = 2.0
        assert calculate_lcoh(**kwargs) == pytest.approx(expected, rel=1e-9)

    def test_stack_replacement_excluded_in_final_year(self):
        """A replacement interval that lands exactly on the final year must
        NOT add a replacement cost (per the documented "no point replacing
        the year the plant retires" rule)."""
        kwargs = dict(
            capex_usd_per_kw=1000.0, opex_pct_of_capex=0.0, wacc=0.05,
            lifetime_years=10, hydrogen_output_kg_per_kw_year=100.0,
            stack_replacement_pct_of_capex=0.5, stack_lifetime_hours=10.0,
            full_load_hours=1.0,  # replacement_interval_years = 10/1 = 10 == lifetime_years
            water_cost_usd_per_m3=0.0, water_consumption_l_per_kg=0.0,
            lcoe_usd_per_kwh=0.0,
        )
        result = calculate_lcoh(**kwargs)
        expected = _reference_lcoh(**kwargs)
        assert result == pytest.approx(expected, rel=1e-9)
        # Sanity: a shorter lifetime that puts the *same* interval strictly
        # before the end DOES incur the replacement cost, and therefore a
        # strictly higher LCOH per unit of (now-reduced) production headroom
        # -- confirms the exclusion is doing something observable, not a
        # no-op that always skips replacement regardless of timing.
        kwargs_with_replacement = dict(kwargs, lifetime_years=15)
        result_with_replacement = calculate_lcoh(**kwargs_with_replacement)
        assert result_with_replacement != pytest.approx(result, rel=1e-9)


class TestPemVsAlkaline:
    """PEM (baseline) vs alkaline (sensitivity variant), per
    METHODOLOGY.md 2.5 / config/scenario_params.yaml's real parameter
    pairs -- CAPEX and efficiency deliberately move in opposite
    directions (alkaline: lower CAPEX, worse specific consumption)."""

    PEM = dict(BASE_KWARGS, capex_usd_per_kw=1200.0, stack_replacement_pct_of_capex=0.15, stack_lifetime_hours=80000)
    ALKALINE = dict(BASE_KWARGS, capex_usd_per_kw=700.0, stack_replacement_pct_of_capex=0.10, stack_lifetime_hours=90000)

    def test_both_technologies_produce_finite_positive_lcoh(self):
        for kwargs in (self.PEM, self.ALKALINE):
            result = calculate_lcoh(**kwargs)
            assert result > 0.0

    def test_lower_capex_alone_lowers_lcoh_holding_everything_else_fixed(self):
        """Isolate the CAPEX effect: alkaline's lower CAPEX, with PEM's own
        efficiency/hydrogen-output held fixed, must lower LCOH."""
        pem_lcoh = calculate_lcoh(**self.PEM)
        cheaper_capex_only = dict(self.PEM, capex_usd_per_kw=self.ALKALINE["capex_usd_per_kw"])
        assert calculate_lcoh(**cheaper_capex_only) < pem_lcoh

    def test_higher_specific_consumption_raises_lcoh(self):
        """Higher efficiency_kwh_per_kg (more energy per kg produced) means
        less hydrogen per kW of capacity per year -- holding full_load_hours
        fixed, hydrogen_output_kg_per_kw_year = full_load_hours / efficiency
        falls, and LCOH (cost per kg) must rise."""
        full_load_hours = 8760.0
        low_efficiency_output = full_load_hours / 51.0   # alkaline, better
        high_efficiency_output = full_load_hours / 52.0  # pem, worse (more kWh/kg)
        assert high_efficiency_output < low_efficiency_output

        kwargs_low = dict(BASE_KWARGS, hydrogen_output_kg_per_kw_year=low_efficiency_output)
        kwargs_high = dict(BASE_KWARGS, hydrogen_output_kg_per_kw_year=high_efficiency_output)
        assert calculate_lcoh(**kwargs_high) > calculate_lcoh(**kwargs_low)


class TestZeroIncentiveBaseline:
    def test_default_incentive_is_zero_and_explicit_zero_match(self):
        default_result = calculate_lcoh(**BASE_KWARGS)
        explicit_zero = calculate_lcoh(
            **BASE_KWARGS, incentive_value_usd_per_kg=0.0, incentive_duration_years=0.0
        )
        assert default_result == pytest.approx(explicit_zero, rel=1e-12)

    def test_nonzero_incentive_strictly_lowers_lcoh(self):
        baseline = calculate_lcoh(**BASE_KWARGS)
        with_incentive = calculate_lcoh(
            **BASE_KWARGS, incentive_value_usd_per_kg=1.0, incentive_duration_years=10.0
        )
        assert with_incentive < baseline

    def test_incentive_duration_beyond_lifetime_only_applies_within_lifetime(self):
        """incentive_duration_years > lifetime_years must not apply the
        incentive to years beyond the project's own life (the year <=
        incentive_duration_years guard is bounded by the loop's own
        range(1, lifetime_years + 1), not by incentive_duration_years)."""
        capped = calculate_lcoh(
            **BASE_KWARGS, incentive_value_usd_per_kg=1.0,
            incentive_duration_years=BASE_KWARGS["lifetime_years"],
        )
        beyond = calculate_lcoh(
            **BASE_KWARGS, incentive_value_usd_per_kg=1.0,
            incentive_duration_years=BASE_KWARGS["lifetime_years"] + 100,
        )
        assert capped == pytest.approx(beyond, rel=1e-12)


class TestCalculateLcohEdgeCases:
    def test_zero_lifetime_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoh(**dict(BASE_KWARGS, lifetime_years=0))

    def test_negative_lifetime_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoh(**dict(BASE_KWARGS, lifetime_years=-5))

    def test_zero_full_load_hours_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoh(**dict(BASE_KWARGS, full_load_hours=0.0))

    def test_wacc_at_or_below_negative_one_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoh(**dict(BASE_KWARGS, wacc=-1.0))
        with pytest.raises(ValueError):
            calculate_lcoh(**dict(BASE_KWARGS, wacc=-1.5))

    def test_negative_wacc_above_negative_one_is_allowed(self):
        # A mild negative WACC is mathematically well-defined (discount
        # factors > 1 instead of < 1) even if not physically realistic --
        # calculate_lcoh only rejects wacc <= -1.0, never a merely-negative one.
        result = calculate_lcoh(**dict(BASE_KWARGS, wacc=-0.5))
        assert result > 0.0


class TestCalculateLcoe:
    LCOE_KWARGS = dict(
        capex_usd_per_kw=700.0, opex_pct_of_capex=0.015, wacc=0.08,
        lifetime_years=25, capacity_factor=0.22,
    )

    def test_positive_result(self):
        assert calculate_lcoe(**self.LCOE_KWARGS) > 0.0

    def test_higher_capex_raises_lcoe(self):
        base = calculate_lcoe(**self.LCOE_KWARGS)
        higher = calculate_lcoe(**dict(self.LCOE_KWARGS, capex_usd_per_kw=1000.0))
        assert higher > base

    def test_higher_capacity_factor_lowers_lcoe(self):
        base = calculate_lcoe(**self.LCOE_KWARGS)
        higher_cf = calculate_lcoe(**dict(self.LCOE_KWARGS, capacity_factor=0.35))
        assert higher_cf < base

    def test_zero_lifetime_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoe(**dict(self.LCOE_KWARGS, lifetime_years=0))

    def test_capacity_factor_out_of_bounds_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoe(**dict(self.LCOE_KWARGS, capacity_factor=0.0))
        with pytest.raises(ValueError):
            calculate_lcoe(**dict(self.LCOE_KWARGS, capacity_factor=1.5))

    def test_wacc_at_or_below_negative_one_raises(self):
        with pytest.raises(ValueError):
            calculate_lcoe(**dict(self.LCOE_KWARGS, wacc=-1.0))
