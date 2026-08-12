"""Offline unit tests for src/economics/decomposition.py.

Uses the real, validated config/scenario_params.yaml via
load_scenario_config() (same pattern as test_topsis.py's
TestDefaultWeightsFromConfig) rather than a hand-built mock ScenarioConfig
-- decomposition.py's functions resolve a large, deeply nested slice of
the real config schema (regions, technologies, electrolyzer, renewables),
and a hand-rolled fixture would either have to duplicate that whole schema
or risk silently drifting from it. calculate_lcoh()/calculate_lcoe()
themselves are already exhaustively tested in isolation in
test_lcoh_model.py; these tests instead target decomposition.py's own
orchestration logic -- region/technology resolution, the Brazil wind CAPEX
override, the WACC-swap invariant, the combined min/baseline/max scenario
range, Monte Carlo, and the input-assumptions disclosure table. The 1D
inversion-point search this file used to test was removed from
decomposition.py (replaced by the 2D competitiveness frontier, ADR-009) --
see tests/test_competitiveness_frontier.py instead.

No network access and no live data downloads: every test here is either
pure config-driven math (real YAML, no disk I/O) or uses monkeypatch/
tmp_path to isolate the one function (decompose_actual_per_region_aggregated)
that reads a file from disk, so results never depend on whatever spatial
pipeline output happens to already exist in data/processed/.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from config.config_loader import load_scenario_config
from src.core.constants import Region
from src.economics import decomposition
from src.economics.lcoh_model import calculate_lcoh


@pytest.fixture(scope="module")
def config():
    return load_scenario_config()


class TestLcohForBaseline:
    @pytest.mark.parametrize(
        "region,renewable_tech",
        [
            (Region.NORDESTE_BR, "solar_pv"),
            (Region.NORDESTE_BR, "onshore_wind"),
            (Region.NORTH_GERMANY, "solar_pv"),
            (Region.NORTH_GERMANY, "onshore_wind"),
        ],
    )
    def test_baseline_lcoh_is_positive_and_finite(self, config, region, renewable_tech):
        lcoh = decomposition._lcoh_for(region, config, renewable_tech=renewable_tech)
        assert math.isfinite(lcoh)
        assert lcoh > 0.0


class TestBrazilWindCapexOverride:
    """Brazil's onshore_wind uses a region-specific CAPEX override
    (1050 USD/kW) instead of the shared global baseline (1300 USD/kW,
    still used by Germany) -- see scenario_params.yaml's
    onshore_wind_capex_override_usd_per_kw comment and
    decomposition._effective_renewable_capex()'s docstring."""

    def test_brazil_uses_region_override_not_global_baseline(self, config):
        region_cfg = decomposition._region_config(Region.NORDESTE_BR, config)
        renewable_cfg = config.renewables.onshore_wind
        effective = decomposition._effective_renewable_capex(region_cfg, renewable_cfg, "onshore_wind")
        assert effective == pytest.approx(1050.0)
        assert effective != pytest.approx(renewable_cfg.capex_usd_per_kw)

    def test_germany_uses_shared_global_baseline(self, config):
        region_cfg = decomposition._region_config(Region.NORTH_GERMANY, config)
        renewable_cfg = config.renewables.onshore_wind
        effective = decomposition._effective_renewable_capex(region_cfg, renewable_cfg, "onshore_wind")
        assert effective == pytest.approx(renewable_cfg.capex_usd_per_kw)

    def test_override_actually_lowers_lcoh_end_to_end(self, config):
        """Confirm the override isn't just resolved in isolation but
        actually propagates through _renewable_lcoe() into _lcoh_for()'s
        final LCOH -- disable it temporarily (restored via try/finally)
        and compare against the real, override-enabled baseline."""
        region_cfg = decomposition._region_config(Region.NORDESTE_BR, config)
        lcoh_with_override = decomposition._lcoh_for(Region.NORDESTE_BR, config, renewable_tech="onshore_wind")

        original_override = region_cfg.onshore_wind_capex_override_usd_per_kw
        region_cfg.onshore_wind_capex_override_usd_per_kw = None
        try:
            lcoh_without_override = decomposition._lcoh_for(
                Region.NORDESTE_BR, config, renewable_tech="onshore_wind"
            )
        finally:
            region_cfg.onshore_wind_capex_override_usd_per_kw = original_override

        assert lcoh_with_override < lcoh_without_override


class TestWaccSwap:
    def test_brazil_using_germany_wacc_lowers_lcoh_below_baseline(self, config):
        baseline = decomposition._lcoh_for(Region.NORDESTE_BR, config, renewable_tech="solar_pv")
        swapped = decomposition.decompose_wacc_swap(
            Region.NORDESTE_BR, Region.NORTH_GERMANY, config, renewable_tech="solar_pv"
        )
        assert swapped["lcoh_usd_per_kg"] < baseline
        assert swapped["wacc_used"] == pytest.approx(config.regions.germany.wacc.solar.baseline)

    def test_wacc_swap_never_changes_region_as_own_capex_multiplier(self, config):
        """CRITICAL INVARIANT documented in decompose_wacc_swap()'s own
        docstring: only WACC travels between regions, never the CAPEX
        multiplier."""
        region_a_cfg = decomposition._region_config(Region.NORDESTE_BR, config)
        result = decomposition.decompose_wacc_swap(
            Region.NORDESTE_BR, Region.NORTH_GERMANY, config, renewable_tech="onshore_wind"
        )
        assert result["capex_multiplier_used"] == pytest.approx(region_a_cfg.capex_multiplier)


class TestScenarioRange:
    def test_baseline_lcoh_falls_within_min_max_bounds(self, config):
        result = decomposition.run_scenario_range(Region.NORTH_GERMANY, config, renewable_tech="solar_pv")
        assert result["lcoh_min"] <= result["lcoh_baseline"] <= result["lcoh_max"]
        assert result["lcoh_min"] <= result["lcoh_max"]

    def test_rows_cover_min_baseline_max_scenarios(self, config):
        result = decomposition.run_scenario_range(Region.NORDESTE_BR, config, renewable_tech="onshore_wind")
        scenario_rows = [r for r in result["rows"] if r["run_type"] == "scenario_range"]
        assert {r["scenario"] for r in scenario_rows} == {"min", "baseline", "max"}


class TestIncentiveDelta:
    def test_germany_production_credit_reduces_lcoh_but_stays_positive(self, config):
        """Reuses decomposition.py's own resolution helpers (never
        duplicating their logic) to build the same calculate_lcoh() inputs
        _lcoh_for() would, but with the region's real, config-sourced
        incentive credit applied -- exactly the composition
        incentive_scenarios.py itself performs."""
        region = Region.NORTH_GERMANY
        renewable_tech = "solar_pv"
        region_cfg = decomposition._region_config(region, config)
        base = decomposition._electrolyzer_defaults(config, "pem")
        wacc = decomposition._tech_wacc(region_cfg, renewable_tech)
        capacity_factor = decomposition._renewable_capacity_factor(config, region, renewable_tech)
        full_load_hours = decomposition._renewable_full_load_hours(config, region, renewable_tech)
        lcoe = decomposition._renewable_lcoe(config, region_cfg, renewable_tech, wacc, capacity_factor)
        hydrogen_output = full_load_hours / base["efficiency_kwh_per_kg"]

        # production_credit_usd_per_kg is deliberately 0.0 in scenario_params.yaml
        # today (incentive scenarios are zeroed pending real programme values --
        # see docs/memory/05_economic_model.md's REHIDRO/IPCEI section), so this
        # test exercises calculate_lcoh()'s incentive-application arithmetic
        # itself with a synthetic non-zero credit rather than asserting on the
        # current (correctly zero) config value.
        incentive_value = 2.0
        incentive_duration = region_cfg.incentives["support_period_years"]

        common_kwargs = dict(
            capex_usd_per_kw=base["capex_usd_per_kw"],
            opex_pct_of_capex=base["opex_pct_of_capex"],
            wacc=wacc,
            lifetime_years=base["lifetime_years"],
            hydrogen_output_kg_per_kw_year=hydrogen_output,
            stack_replacement_pct_of_capex=base["stack_replacement_pct_of_capex"],
            stack_lifetime_hours=base["stack_lifetime_hours"],
            full_load_hours=full_load_hours,
            water_cost_usd_per_m3=base["water_cost_usd_per_m3"],
            water_consumption_l_per_kg=base["water_consumption_l_per_kg"],
            lcoe_usd_per_kwh=lcoe,
            capex_multiplier=region_cfg.capex_multiplier,
        )
        baseline_lcoh = calculate_lcoh(**common_kwargs, incentive_value_usd_per_kg=0.0, incentive_duration_years=0.0)
        incentivized_lcoh = calculate_lcoh(
            **common_kwargs,
            incentive_value_usd_per_kg=incentive_value,
            incentive_duration_years=incentive_duration,
        )
        assert 0.0 < incentivized_lcoh < baseline_lcoh


class TestMonteCarloOutput:
    def test_output_structure_and_ci_bounds(self, config):
        result = decomposition.run_lcoh_monte_carlo(
            Region.NORTH_GERMANY, config, renewable_tech="solar_pv", n_samples=200, seed=1
        )
        for key in ("lcoh_mean", "lcoh_std", "lcoh_ci_lower", "lcoh_ci_upper", "n_samples", "lcoh_samples"):
            assert key in result
        assert result["n_samples"] == 200
        assert len(result["lcoh_samples"]) == 200
        assert result["lcoh_ci_lower"] <= result["lcoh_mean"] <= result["lcoh_ci_upper"]
        assert result["lcoh_std"] >= 0.0
        assert np.all(np.isfinite(result["lcoh_samples"]))

    def test_compute_lcoh_with_uncertainty_baseline_matches_lcoh_for(self, config):
        result = decomposition.compute_lcoh_with_uncertainty(
            Region.NORDESTE_BR, config, renewable_tech="onshore_wind", n_samples=100, seed=2
        )
        expected_baseline = decomposition._lcoh_for(Region.NORDESTE_BR, config, renewable_tech="onshore_wind")
        assert result["lcoh_baseline_usd_per_kg"] == pytest.approx(expected_baseline, rel=1e-9)


class TestInputAssumptions:
    EXPECTED_COLUMNS = {
        "region", "renewable_tech", "electrolyzer_technology",
        "capex_electrolyzer_usd_per_kw", "capex_multiplier", "wacc",
        "lcoe_renewable_usd_per_kwh", "capacity_factor", "specific_energy_kwh_per_kg",
        "lifetime_years", "opex_pct_capex", "stack_replacement_pct_capex",
        "water_cost_usd_per_kg", "incentive_credit_usd_per_kg", "incentive_duration_years",
    }

    def test_two_rows_one_per_electrolyzer_technology(self, config):
        df = decomposition.get_input_assumptions(Region.NORTH_GERMANY, "solar_pv", config)
        assert set(df["electrolyzer_technology"]) == {"pem", "alkaline"}
        assert len(df) == 2

    def test_columns_match_documented_schema(self, config):
        df = decomposition.get_input_assumptions(Region.NORDESTE_BR, "onshore_wind", config)
        assert self.EXPECTED_COLUMNS.issubset(set(df.columns))

    def test_discloses_incentive_without_it_affecting_the_baseline_lcoh(self, config):
        """get_input_assumptions() discloses the region's hypothetical
        incentive for reference, but decompose_actual()'s own baseline LCOH
        must remain incentive-free (see _lcoh_for's docstring) -- the two
        must not silently drift into contradicting each other."""
        region_cfg = decomposition._region_config(Region.NORTH_GERMANY, config)
        df = decomposition.get_input_assumptions(Region.NORTH_GERMANY, "solar_pv", config)
        assert (df["incentive_credit_usd_per_kg"] == region_cfg.incentives["production_credit_usd_per_kg"]).all()

        row = df[df["electrolyzer_technology"] == "pem"].iloc[0]
        assert row["wacc"] == pytest.approx(decomposition._tech_wacc(region_cfg, "solar_pv"))


class TestDecomposeActual:
    def test_returns_expected_keys_and_matches_lcoh_for(self, config):
        result = decomposition.decompose_actual(Region.NORTH_GERMANY, config, renewable_tech="solar_pv")
        expected_keys = {
            "run_type", "region", "renewable_tech", "wacc_used", "capex_multiplier_used",
            "lcoh_usd_per_kg", "lcoh_mean", "lcoh_std", "lcoh_ci_lower", "lcoh_ci_upper",
        }
        assert expected_keys.issubset(result.keys())
        assert result["run_type"] == "actual"
        expected_lcoh = decomposition._lcoh_for(Region.NORTH_GERMANY, config, renewable_tech="solar_pv")
        assert result["lcoh_usd_per_kg"] == pytest.approx(expected_lcoh, rel=1e-9)


class TestDecomposeActualPerRegionAggregatedErrors:
    def test_invalid_tech_raises_value_error(self, config):
        with pytest.raises(ValueError):
            decomposition.decompose_actual_per_region_aggregated(Region.NORDESTE_BR, "not_a_real_tech", config)

    def test_missing_h2_potential_file_raises_file_not_found(self, config, tmp_path, monkeypatch):
        # Isolates this test from whatever h2_potential_*.geojson files may
        # or may not already exist in the real data/processed/ directory.
        monkeypatch.setattr(decomposition, "H2_POTENTIAL_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            decomposition.decompose_actual_per_region_aggregated(Region.NORDESTE_BR, "solar", config)


class TestRegionAndTechResolution:
    def test_region_to_config_attr_mapping(self, config):
        assert decomposition._region_config(Region.NORDESTE_BR, config) is config.regions.brazil
        assert decomposition._region_config(Region.NORTH_GERMANY, config) is config.regions.germany

    def test_tech_wacc_resolves_solar_and_wind_separately(self, config):
        region_cfg = config.regions.brazil
        assert decomposition._tech_wacc(region_cfg, "solar_pv") == pytest.approx(region_cfg.wacc.solar.baseline)
        assert decomposition._tech_wacc(region_cfg, "onshore_wind") == pytest.approx(region_cfg.wacc.wind.baseline)

    def test_tech_wacc_resolves_min_max_scenario_branches_inverted(self, config):
        """Combined-scenario "min" (worst case) means the HIGHEST WACC (the
        RangeParam's own `max`), "max" (best case) means the LOWEST (the
        RangeParam's own `min`) -- inverted relative to every other
        RangeParam consumer in this codebase (see
        config_loader.resolve_inverted_param())."""
        region_cfg = config.regions.brazil
        assert decomposition._tech_wacc(region_cfg, "solar_pv", scenario="min") == pytest.approx(
            region_cfg.wacc.solar.max
        )
        assert decomposition._tech_wacc(region_cfg, "solar_pv", scenario="max") == pytest.approx(
            region_cfg.wacc.solar.min
        )

    def test_tech_wacc_unknown_technology_raises(self, config):
        region_cfg = config.regions.brazil
        with pytest.raises(ValueError):
            decomposition._tech_wacc(region_cfg, "not_a_real_tech")
