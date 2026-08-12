"""Offline unit tests for src/spatial/topsis.py's pure math and weight
helpers: `_vectorized_topsis`, `perturb_topsis_weights`,
`_get_default_weights`. Deliberately does NOT exercise `run_topsis()`
itself (that requires real criterion rasters via data_layers.py, not
available without live acquisition in this environment) -- these tests
target the vectorized math and weight-handling logic in isolation, which
is where a correctness bug would actually live.
"""

from __future__ import annotations

import numpy as np
import pytest

from config.config_loader import load_scenario_config
from src.spatial.topsis import (
    CRITERIA,
    CRITERION_DIRECTION,
    _get_default_weights,
    _vectorized_topsis,
    perturb_topsis_weights,
)


class TestVectorizedTopsisBoundaryConditions:
    def test_dominant_row_scores_exactly_one_dominated_scores_exactly_zero(self):
        # Row 0 dominates on both criteria (higher benefit, lower cost);
        # row 2 is dominated on both. Row 1 is intermediate.
        matrix = np.array([[10.0, 1.0], [5.0, 5.0], [1.0, 10.0]])
        weights = np.array([0.5, 0.5])
        scores = _vectorized_topsis(matrix, weights, ("benefit", "cost"))

        assert scores[0] == pytest.approx(1.0, abs=1e-9)
        assert scores[2] == pytest.approx(0.0, abs=1e-9)
        assert scores[0] > scores[1] > scores[2]

    def test_single_benefit_criterion_ordering(self):
        matrix = np.array([[1.0], [5.0], [10.0]])
        weights = np.array([1.0])
        scores = _vectorized_topsis(matrix, weights, ("benefit",))
        assert scores[2] > scores[1] > scores[0]
        assert scores[2] == pytest.approx(1.0, abs=1e-9)
        assert scores[0] == pytest.approx(0.0, abs=1e-9)

    def test_single_cost_criterion_ordering_is_reversed(self):
        matrix = np.array([[1.0], [5.0], [10.0]])
        weights = np.array([1.0])
        scores = _vectorized_topsis(matrix, weights, ("cost",))
        assert scores[0] > scores[1] > scores[2]
        assert scores[0] == pytest.approx(1.0, abs=1e-9)
        assert scores[2] == pytest.approx(0.0, abs=1e-9)

    def test_constant_column_does_not_crash_and_stays_finite(self):
        """A criterion with zero variance (norms==0 guard) must not divide
        by zero -- all rows tie on that criterion, so it should not by
        itself determine the ranking."""
        matrix = np.array([[5.0, 1.0], [5.0, 5.0], [5.0, 10.0]])
        weights = np.array([0.5, 0.5])
        scores = _vectorized_topsis(matrix, weights, ("benefit", "cost"))
        assert np.all(np.isfinite(scores))
        # Ranking is now driven entirely by the second (cost) criterion.
        assert scores[0] > scores[1] > scores[2]


class TestVectorizedTopsisMonotonicity:
    def test_increasing_a_benefit_value_never_decreases_its_score(self):
        base_matrix = np.array([[3.0, 5.0], [3.0, 5.0], [3.0, 5.0]])
        base_matrix[:, 0] = [1.0, 5.0, 9.0]  # increasing benefit criterion
        weights = np.array([0.6, 0.4])
        scores = _vectorized_topsis(base_matrix, weights, ("benefit", "cost"))
        assert scores[0] <= scores[1] <= scores[2]

    def test_increasing_a_cost_value_never_increases_its_score(self):
        matrix = np.array([[5.0, 1.0], [5.0, 5.0], [5.0, 9.0]])
        weights = np.array([0.4, 0.6])
        scores = _vectorized_topsis(matrix, weights, ("benefit", "cost"))
        assert scores[0] >= scores[1] >= scores[2]


class TestPerturbTopsisWeights:
    BASE_WEIGHTS = {
        "resource": 0.35, "distance_to_grid": 0.20,
        "distance_to_water": 0.25, "slope": 0.20,
    }

    def test_positive_perturbation_hits_exact_target_and_sums_to_one(self):
        perturbed = perturb_topsis_weights(self.BASE_WEIGHTS, "resource", 0.20)
        assert perturbed["resource"] == pytest.approx(0.42, abs=1e-9)
        assert sum(perturbed.values()) == pytest.approx(1.0, abs=1e-9)

    def test_negative_perturbation_hits_exact_target_and_sums_to_one(self):
        perturbed = perturb_topsis_weights(self.BASE_WEIGHTS, "resource", -0.20)
        assert perturbed["resource"] == pytest.approx(0.28, abs=1e-9)
        assert sum(perturbed.values()) == pytest.approx(1.0, abs=1e-9)

    def test_remaining_weights_keep_relative_proportions(self):
        perturbed = perturb_topsis_weights(self.BASE_WEIGHTS, "resource", 0.20)
        # Original ratio distance_to_water : slope was 0.25 : 0.20 = 1.25.
        ratio = perturbed["distance_to_water"] / perturbed["slope"]
        assert ratio == pytest.approx(0.25 / 0.20, rel=1e-9)

    def test_unknown_criterion_raises(self):
        with pytest.raises(ValueError):
            perturb_topsis_weights(self.BASE_WEIGHTS, "not_a_real_criterion", 0.20)

    def test_extreme_negative_delta_clamps_at_zero_not_negative(self):
        perturbed = perturb_topsis_weights(self.BASE_WEIGHTS, "resource", -1.5)
        assert perturbed["resource"] == pytest.approx(0.0, abs=1e-9)
        assert sum(perturbed.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(v >= 0.0 for v in perturbed.values())

    def test_extreme_positive_delta_clamps_at_one(self):
        perturbed = perturb_topsis_weights(self.BASE_WEIGHTS, "resource", 10.0)
        assert perturbed["resource"] == pytest.approx(1.0, abs=1e-9)
        assert sum(perturbed.values()) == pytest.approx(1.0, abs=1e-9)


class TestDefaultWeightsFromConfig:
    """Confirms topsis.py's documented WEIGHT-FIELD MAPPING NOTE against
    the real, currently-loaded scenario_params.yaml -- if the YAML's field
    names or values ever drift, this test (not just the module docstring)
    will catch it."""

    def test_weight_field_mapping_matches_config(self):
        config = load_scenario_config()
        weights = _get_default_weights(config)

        assert set(weights) == set(CRITERIA)
        assert weights["resource"] == config.topsis.weights.resource_quality
        assert weights["distance_to_grid"] == config.topsis.weights.grid_distance
        assert weights["distance_to_water"] == config.topsis.weights.proximity_infrastructure
        assert weights["slope"] == config.topsis.weights.land_availability

    def test_default_weights_sum_to_one(self):
        config = load_scenario_config()
        weights = _get_default_weights(config)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)


class TestCriteriaDirections:
    def test_resource_is_the_only_benefit_criterion(self):
        assert CRITERION_DIRECTION["resource"] == "benefit"
        for criterion in CRITERIA:
            if criterion != "resource":
                assert CRITERION_DIRECTION[criterion] == "cost"

    def test_criterion_direction_covers_every_criterion(self):
        assert set(CRITERION_DIRECTION) == set(CRITERIA)
