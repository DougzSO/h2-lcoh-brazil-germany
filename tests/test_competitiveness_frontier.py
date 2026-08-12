"""Offline unit tests for src/economics/competitiveness_frontier.py.

Uses the real, validated config/scenario_params.yaml via
load_scenario_config() (same pattern as test_decomposition.py) -- the
frontier module resolves the same deeply nested config slice
decomposition.py does. Replaces the old tests/test_decomposition.py::
TestInversionPoint (solar converges, wind does not), which targeted the
1D find_inversion_point() removed in ADR-009.

No network access, pure config-driven math. compute_frontier() writes a
real CSV to outputs/tables/ (same convention decomposition.py's own tests
implicitly rely on via run_all_decompositions()) -- not isolated to
tmp_path, since this mirrors how the module is actually used by
run_pipeline.py; kept fast by using a small grid via a monkeypatched
wacc_steps.
"""

from __future__ import annotations

import math

import pytest

from config.config_loader import load_scenario_config
from src.economics import competitiveness_frontier as cf


@pytest.fixture(scope="module")
def config():
    cfg = load_scenario_config()
    # Small grid for test speed -- CompetitivenessFrontierConfig is a
    # pydantic model, so mutate the already-validated instance directly
    # rather than reloading with a modified YAML.
    cfg.competitiveness_frontier.wacc_steps = 5
    return cfg


class TestComputeFrontier:
    @pytest.mark.parametrize("renewable_tech", ["solar_pv", "onshore_wind"])
    def test_grid_shape_and_columns(self, config, renewable_tech):
        df = cf.compute_frontier(config, renewable_tech)
        steps = config.competitiveness_frontier.wacc_steps
        assert len(df) == steps * steps
        assert set(df.columns) == {
            "technology", "wacc_br", "wacc_de", "lcoh_br", "lcoh_de",
            "delta_lcoh", "parity_flag",
        }
        assert (df["technology"] == renewable_tech).all()
        assert df["lcoh_br"].apply(math.isfinite).all()
        assert df["lcoh_de"].apply(math.isfinite).all()

    def test_delta_lcoh_is_br_minus_de(self, config):
        df = cf.compute_frontier(config, "solar_pv")
        computed = df["lcoh_br"] - df["lcoh_de"]
        assert (df["delta_lcoh"] - computed).abs().max() < 1e-9

    def test_invalid_renewable_tech_raises(self, config):
        with pytest.raises(ValueError):
            cf.compute_frontier(config, "not_a_real_tech")


class TestSummarizeFrontier:
    def test_solar_has_a_parity_point_and_matches_historical_inversion_result(self, config):
        """Solar's frontier must contain a parity region, and the
        WACC-gap-closure root-find (fixing Germany at baseline) must land
        close to the pre-refactor 1D inversion search's own verified
        result (WACC=11.92%, see docs/memory/05_economic_model.md) --
        confirms the 2D frontier reduces to the same root along that slice."""
        df = cf.compute_frontier(config, "solar_pv")
        summary = cf.summarize_frontier(config, "solar_pv", df)

        assert summary["gap_closure_parity_br_wacc"] is not None
        assert summary["gap_closure_parity_br_wacc"] == pytest.approx(0.1192, abs=5e-3)
        assert summary["no_frontier_direction"] is None

    def test_wind_reports_no_frontier_brazil_always_more_expensive(self, config):
        """No sign change across Brazil's full tested wind WACC range at
        Germany's baseline WACC -- matches the pre-refactor 1D search's own
        converged=False result for wind (METHODOLOGY.md 2.6)."""
        df = cf.compute_frontier(config, "onshore_wind")
        summary = cf.summarize_frontier(config, "onshore_wind", df)

        assert summary["gap_closure_parity_br_wacc"] is None
        assert summary["no_frontier_direction"] == "always more expensive"

    def test_direct_support_equals_baseline_lcoh_difference(self, config):
        df = cf.compute_frontier(config, "onshore_wind")
        summary = cf.summarize_frontier(config, "onshore_wind", df)
        expected = summary["lcoh_br_baseline"] - summary["lcoh_de_baseline"]
        assert summary["direct_support_usd_per_kg"] == pytest.approx(expected, rel=1e-9)

    def test_invalid_renewable_tech_raises(self, config):
        with pytest.raises(ValueError):
            cf.summarize_frontier(config, "not_a_real_tech", None)


class TestRunAllFrontiers:
    def test_runs_both_technologies_and_writes_csvs(self, config, tmp_path, monkeypatch):
        monkeypatch.setattr(cf, "OUTPUT_DIR", str(tmp_path))
        results = cf.run_all_frontiers(config)
        assert set(results.keys()) == {"solar_pv", "onshore_wind"}
        for tech, r in results.items():
            assert not r["frontier"].empty
            assert (tmp_path / f"competitiveness_frontier_{tech}.csv").exists()


class TestDirectSupportByScenario:
    """direct_support_by_scenario()/direct_support_range() (ADR-013):
    the direct-support metric extended to all three combined scenarios."""

    @pytest.mark.parametrize("renewable_tech", ["solar_pv", "onshore_wind"])
    def test_returns_exactly_the_three_combined_scenarios(self, config, renewable_tech):
        by_scenario = cf.direct_support_by_scenario(config, renewable_tech)
        assert set(by_scenario.keys()) == {"min", "baseline", "max"}
        assert all(math.isfinite(v) for v in by_scenario.values())

    def test_invalid_renewable_tech_raises(self, config):
        with pytest.raises(ValueError):
            cf.direct_support_by_scenario(config, "not_a_real_tech")

    @pytest.mark.parametrize("renewable_tech", ["solar_pv", "onshore_wind"])
    def test_range_brackets_baseline_and_matches_by_scenario_values(self, config, renewable_tech):
        result = cf.direct_support_range(config, renewable_tech)
        by_scenario = result["by_scenario_usd_per_kg"]
        assert result["range_min_usd_per_kg"] == min(by_scenario.values())
        assert result["range_max_usd_per_kg"] == max(by_scenario.values())
        assert result["range_min_usd_per_kg"] <= result["baseline_usd_per_kg"] <= result["range_max_usd_per_kg"]
        assert result["baseline_usd_per_kg"] == pytest.approx(by_scenario["baseline"], rel=1e-9)

    def test_summarize_frontier_baseline_direct_support_matches_direct_support_range(self, config):
        """summarize_frontier()'s single-scenario direct_support_usd_per_kg
        must be bit-for-bit direct_support_range()'s own baseline component
        -- the two must never silently diverge onto two numbers for the
        same technology (see this module's own docstring)."""
        df = cf.compute_frontier(config, "solar_pv")
        summary = cf.summarize_frontier(config, "solar_pv", df)
        range_result = cf.direct_support_range(config, "solar_pv")
        assert summary["direct_support_usd_per_kg"] == pytest.approx(
            range_result["baseline_usd_per_kg"], rel=1e-9
        )


class TestInterpretDirectSupportRange:
    """_interpret_direct_support_range(): the three interpretation cases,
    exercised directly on synthetic min/max values (no config needed) so
    each branch is tested regardless of what the real literature ranges
    currently produce."""

    def test_entirely_negative_range_reports_advantage(self):
        text = cf._interpret_direct_support_range(-2.0, -0.5)
        assert "advantage" in text
        assert "no production incentive needed" in text
        assert "$0.50" in text and "$2.00" in text

    def test_entirely_negative_equal_endpoints_reports_single_value(self):
        text = cf._interpret_direct_support_range(-1.0, -1.0)
        assert "$1.00/kg natural cost advantage" in text

    def test_entirely_positive_range_reports_incentive_needed(self):
        text = cf._interpret_direct_support_range(0.5, 2.0)
        assert "needs a production incentive" in text
        assert "$0.50" in text and "$2.00" in text

    def test_straddling_zero_reports_worst_and_best_case(self):
        text = cf._interpret_direct_support_range(-0.90, 2.92)
        assert "worst case Brazil needs $2.92/kg" in text
        assert "best case it has $0.90/kg advantage" in text
