"""
config_loader.py

Pydantic-based loader/validator for scenario_params.yaml (hydrogen LCOH study).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from pydantic import BaseModel, field_validator, model_validator


# --------------------------------------------------------------------------
# Literature-validated {min, baseline, max} range for a single physical or
# financial parameter. Every consumer resolves this to a scalar via
# resolve_param() (direct branch read) or resolve_inverted_param() (WACC's
# combined-scenario branches invert -- see that function's docstring below).
# --------------------------------------------------------------------------

class RangeParam(BaseModel):
    """A literature-validated {min, baseline, max} range for a single
    physical parameter. Every consumer must resolve this to a scalar via
    resolve_param() below -- there is deliberately no default/fallback
    scalar reader, so a caller that forgets to resolve a RangeParam fails
    immediately and loudly (e.g. a stray arithmetic op on the object)
    rather than silently using a wrong branch."""
    min: float
    baseline: float
    max: float

    @model_validator(mode="after")
    def check_order(self) -> "RangeParam":
        if not (self.min <= self.baseline <= self.max):
            raise ValueError(
                f"RangeParam order violated: min={self.min} <= "
                f"baseline={self.baseline} <= max={self.max} must hold"
            )
        return self


# --------------------------------------------------------------------------
# Regions
# --------------------------------------------------------------------------

class RegionWaccConfig(BaseModel):
    """Per-technology WACC as a literature-validated {min, baseline, max}
    RangeParam (replaces the old scalar solar/wind + shared `range` tuple).
    Combined-scenario resolution is INVERTED relative to every other
    RangeParam consumer in this codebase -- see resolve_inverted_param()."""
    solar: RangeParam
    wind: RangeParam


class BrazilIncentives(BaseModel):
    tax_credit_pct: float
    production_credit_usd_per_kg: float


class GermanyIncentives(BaseModel):
    production_credit_usd_per_kg: float
    support_period_years: int


class RegionConfig(BaseModel):
    name: str
    grid_crs: str
    wacc: RegionWaccConfig
    capex_multiplier: float
    incentives: Dict[str, float]
    # Optional region-specific override for renewables.<tech>'s otherwise-
    # shared capex_usd_per_kw (RenewablesConfig has no region axis -- see
    # RenewablesConfig below). None (the default for any region that does
    # not set the field in the YAML) means "use the shared baseline".
    # Brazil sets onshore_wind's (IRENA/EPE competitive Northeast benchmark)
    # and, since ADR-012, solar_pv's too -- not to change Brazil's own
    # solar CAPEX, but to PRESERVE it unchanged after renewables.solar_pv's
    # shared value was updated for Germany (ABUZAYED & HARTMANN, 2022);
    # see docs/memory/05_economic_model.md.
    onshore_wind_capex_override_usd_per_kw: Optional[float] = None
    solar_pv_capex_override_usd_per_kw: Optional[float] = None
    # Optional region-specific override for the COMBINED min/max scenario
    # branches' renewable CAPEX (the range-scenario sibling of the
    # single-value overrides above, which only cover the baseline branch).
    # None (default) means non-baseline branches fall back to the shared
    # renewables.<tech>.capex_range.
    onshore_wind_capex_range_override_usd_per_kw: Optional[Tuple[float, float]] = None
    solar_pv_capex_range_override_usd_per_kw: Optional[Tuple[float, float]] = None

    @field_validator("capex_multiplier")
    @classmethod
    def check_capex_multiplier_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"capex_multiplier must be > 0, got {v}")
        return v

    @field_validator(
        "onshore_wind_capex_range_override_usd_per_kw",
        "solar_pv_capex_range_override_usd_per_kw",
    )
    @classmethod
    def check_capex_range_override_order(
        cls, v: Optional[Tuple[float, float]]
    ) -> Optional[Tuple[float, float]]:
        if v is not None and v[0] >= v[1]:
            raise ValueError(f"capex range override must have min < max, got {v}")
        return v


class RegionsConfig(BaseModel):
    brazil: RegionConfig
    germany: RegionConfig

    @model_validator(mode="after")
    def check_required_regions(self) -> "RegionsConfig":
        if self.brazil is None or self.germany is None:
            raise ValueError("Both 'brazil' and 'germany' regions are required")
        return self


# --------------------------------------------------------------------------
# Electrolyzer
# --------------------------------------------------------------------------

class ElectrolyzerTechConfig(BaseModel):
    capex_usd_per_kw: float
    efficiency_kwh_per_kg: float
    stack_replacement_pct_of_capex: float
    stack_lifetime_hours: int

    @field_validator("stack_replacement_pct_of_capex")
    @classmethod
    def check_pct_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"stack_replacement_pct_of_capex must be in [0,1], got {v}"
            )
        return v


class ElectrolyzerSharedConfig(BaseModel):
    opex_pct_of_capex: float
    lifetime_years: int
    water_cost_usd_per_m3: float
    water_consumption_l_per_kg: float


class ElectrolyzerConfig(BaseModel):
    technologies: Dict[str, ElectrolyzerTechConfig]
    shared: ElectrolyzerSharedConfig

    @field_validator("technologies")
    @classmethod
    def check_known_technologies(
        cls, v: Dict[str, ElectrolyzerTechConfig]
    ) -> Dict[str, ElectrolyzerTechConfig]:
        required = {"pem", "alkaline"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"electrolyzer.technologies missing: {missing}")
        return v


# --------------------------------------------------------------------------
# Renewables
# --------------------------------------------------------------------------

class RenewableTechConfig(BaseModel):
    capex_usd_per_kw: float
    capex_range: Tuple[float, float]
    lifetime_years: int
    opex_pct_of_capex: float

    @field_validator("capex_range")
    @classmethod
    def check_capex_range_order(cls, v: Tuple[float, float]) -> Tuple[float, float]:
        if v[0] >= v[1]:
            raise ValueError(f"capex_range must have min < max, got {v}")
        return v


class RenewablesConfig(BaseModel):
    solar_pv: RenewableTechConfig
    onshore_wind: RenewableTechConfig


# --------------------------------------------------------------------------
# Technologies: literature-validated {min, baseline, max} ranges for
# capacity factor, full-load hours, and capacity density -- replaces the
# scalar RegionRenewableOverride / RenewableTechConfig.capacity_factor_default
# / RenewableTechConfig.power_density_w_per_m2 fields removed above. See
# scenario_params.yaml's own `technologies:` block comment and
# docs/memory/09_methodology_assumptions.md /
# docs/memory/05_capacity_density_assumptions.md for the literature review
# and study-specific rationale behind every number.
# --------------------------------------------------------------------------

class TechRegionParams(BaseModel):
    capacity_factor: RangeParam
    full_load_hours: RangeParam
    capacity_density_mw_per_km2: RangeParam

    @model_validator(mode="after")
    def check_full_load_hours_consistency(self) -> "TechRegionParams":
        """full_load_hours is stored explicitly (not just derived inline
        by every caller) so consumers can resolve_param() it directly --
        this cross-checks it against capacity_factor * 8760 at load time
        so the two can never silently drift apart in scenario_params.yaml."""
        for branch in ("min", "baseline", "max"):
            cf = getattr(self.capacity_factor, branch)
            flh = getattr(self.full_load_hours, branch)
            expected = cf * 8760.0
            if abs(flh - expected) > 1.0:  # 1-hour tolerance for YAML rounding
                raise ValueError(
                    f"full_load_hours.{branch}={flh} inconsistent with "
                    f"capacity_factor.{branch}={cf} (expected {expected:.1f} "
                    f"= capacity_factor * 8760)"
                )
        return self


class TechnologyRegionsParams(BaseModel):
    brazil: TechRegionParams
    germany: TechRegionParams


class TechnologiesConfig(BaseModel):
    solar_pv: TechnologyRegionsParams
    onshore_wind: TechnologyRegionsParams


def resolve_param(param: RangeParam, scenario: str) -> float:
    """
    Resolve a literature-validated RangeParam to a scalar for the active
    scenario branch. No fallback, no default scenario -- every caller must
    pass a RangeParam and a valid branch name explicitly.

    ADAPTED from this migration's originally-specified dict-based
    signature (`resolve_param(param: dict, scenario='baseline')`): this
    codebase's config is a validated Pydantic ScenarioConfig, not a raw
    dict (Hard Rule 4, root CLAUDE.md -- config_loader.py is the only YAML
    gateway), so this reads Pydantic attributes instead of dict keys.
    Semantics are otherwise identical, and the failure mode this
    migration cares about is preserved: passing a bare float/scalar
    (the exact old-style value this refactor eliminates) raises TypeError
    immediately rather than being silently accepted.

    Parameters
    ----------
    param : RangeParam
    scenario : str
        The combined scenario branch: one of "min"/"baseline"/"max",
        passed explicitly by every caller -- there is no global
        config-wide selector to fall back to.

    Returns
    -------
    float

    Raises
    ------
    TypeError
        If `param` is not a RangeParam.
    ValueError
        If `scenario` is not "min", "baseline", or "max".
    """
    if not isinstance(param, RangeParam):
        raise TypeError(
            f"Expected a RangeParam (min/baseline/max), got {type(param).__name__}. "
            f"All scalar capacity_factor/capacity_density/full_load_hours "
            f"parameters were migrated to RangeParam dicts in "
            f"scenario_params.yaml -- see "
            f"docs/memory/09_methodology_assumptions.md."
        )
    if scenario not in ("min", "baseline", "max"):
        raise ValueError(f"scenario must be one of 'min'/'baseline'/'max', got {scenario!r}")
    return float(getattr(param, scenario))


# Combined-scenario branch -> RangeParam attribute, for parameters where
# "worst case" means the HIGH end of the range rather than the low end
# (financing cost: worse = higher WACC). This is the inverse of every other
# RangeParam consumer in this codebase (capacity_factor/density: "min"
# scenario directly reads .min, since worse = lower resource quality).
_INVERTED_SCENARIO_BRANCH = {"min": "max", "baseline": "baseline", "max": "min"}


def resolve_inverted_param(param: RangeParam, scenario: str) -> float:
    """
    Resolve a RangeParam whose combined-scenario direction is INVERTED
    relative to resolve_param() above -- used for WACC only. The combined
    "min" scenario (worst case, everything simultaneously) means the
    HIGHEST financing cost, i.e. this RangeParam's own `max` branch; the
    combined "max" scenario (best case) means the LOWEST financing cost,
    i.e. this RangeParam's own `min` branch. "baseline" is unaffected.

    Parameters
    ----------
    param : RangeParam
        A region+technology's WACC RangeParam (RegionWaccConfig.solar/.wind).
    scenario : str
        One of "min"/"baseline"/"max" -- the COMBINED scenario branch, not
        a RangeParam attribute name directly.

    Returns
    -------
    float

    Raises
    ------
    TypeError
        If `param` is not a RangeParam.
    ValueError
        If `scenario` is not "min", "baseline", or "max".
    """
    if not isinstance(param, RangeParam):
        raise TypeError(
            f"Expected a RangeParam (min/baseline/max), got {type(param).__name__}."
        )
    if scenario not in _INVERTED_SCENARIO_BRANCH:
        raise ValueError(f"scenario must be one of 'min'/'baseline'/'max', got {scenario!r}")
    return float(getattr(param, _INVERTED_SCENARIO_BRANCH[scenario]))


# --------------------------------------------------------------------------
# TOPSIS
# --------------------------------------------------------------------------

class TopsisWeights(BaseModel):
    resource_quality: float
    proximity_infrastructure: float
    land_availability: float
    grid_distance: float

    @model_validator(mode="after")
    def check_weights_sum_to_one(self) -> "TopsisWeights":
        total = (
            self.resource_quality
            + self.proximity_infrastructure
            + self.land_availability
            + self.grid_distance
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"topsis.weights must sum to 1.0, got {total}")
        return self


class TopsisNormalization(BaseModel):
    """Robustness-check knob only (see scenario_params.yaml's inline note):
    "vector" is the published, primary normalization and must stay the
    default; "adaptive" is opt-in via topsis.run_topsis(norm_method=...)."""
    method: str = "vector"
    cv_threshold: float = 0.10
    # If True, run_pipeline.py's spatial stage additionally runs the
    # adaptive variant per region/tech purely as a side-by-side robustness
    # comparison (logged + written to outputs/tables/, see run_pipeline.py's
    # stage_spatial()) -- it never feeds into site_selection/h2_potential.
    run_adaptive_robustness_check: bool = True

    @field_validator("method")
    @classmethod
    def check_method(cls, v: str) -> str:
        if v not in ("vector", "adaptive"):
            raise ValueError(f"topsis.normalization.method must be 'vector' or 'adaptive', got {v!r}")
        return v


class TopsisConfig(BaseModel):
    weights: TopsisWeights
    normalization: TopsisNormalization = TopsisNormalization()


# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------

class ProtectedAreaBufferConfig(BaseModel):
    # Per-region buffer distance (km), not a shared scalar (ADR-011): Brazil's
    # federal protected areas (UCs federais) are strict no-use reserves,
    # while Germany's protected-areas dataset is Natura 2000, which permits
    # sustainable renewable-energy development under EU law -- a uniform
    # 2 km buffer excluded ~60% of the German study region, inconsistent
    # with actual German RE siting practice. See
    # docs/memory/04_spatial_methodology.md.
    brazil: float
    germany: float

    @field_validator("brazil", "germany")
    @classmethod
    def check_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"protected-area buffer distance must be >= 0, got {v}")
        return v


class ThresholdsConfig(BaseModel):
    # min_capacity_factor, max_slope_degrees, and max_distance_to_grid_km
    # were removed here (see ADR-009, 06_technical_decisions_log.md):
    # confirmed unused anywhere in src/ -- slope and grid distance are
    # TOPSIS-weighted continuous criteria (METHODOLOGY.md 2.3), never binary
    # cutoffs, and min_capacity_factor was never wired into any exclusion
    # logic. min_distance_to_protected_area_km is the one survivor: it is
    # now an ACTIVE buffer distance consumed by exclusion_mask.py (ADR-010),
    # region-specific rather than a shared scalar (ADR-011).
    min_distance_to_protected_area_km: ProtectedAreaBufferConfig
    min_contiguous_suitable_area_km2: float
    max_contiguous_suitable_area_km2: float

    @field_validator("max_contiguous_suitable_area_km2")
    @classmethod
    def check_max_area_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_contiguous_suitable_area_km2 must be > 0, got {v}")
        return v


# --------------------------------------------------------------------------
# Sensitivity
# --------------------------------------------------------------------------

class SensitivityConfig(BaseModel):
    wacc_steps: int
    capex_multiplier_range: Tuple[float, float]
    capex_multiplier_steps: int
    monte_carlo_iterations: int

    @field_validator("capex_multiplier_range")
    @classmethod
    def check_capex_multiplier_range_order(
        cls, v: Tuple[float, float]
    ) -> Tuple[float, float]:
        if v[0] >= v[1]:
            raise ValueError(f"capex_multiplier_range must have min < max, got {v}")
        return v


# --------------------------------------------------------------------------
# Competitiveness frontier (replaces the old 1D inversion_search block --
# see ADR-009, 06_technical_decisions_log.md)
# --------------------------------------------------------------------------

class CompetitivenessFrontierConfig(BaseModel):
    wacc_steps: int
    parity_tolerance_pct: float

    @field_validator("wacc_steps")
    @classmethod
    def check_wacc_steps_range(cls, v: int) -> int:
        if not (15 <= v <= 20):
            raise ValueError(f"competitiveness_frontier.wacc_steps must be in [15, 20], got {v}")
        return v

    @field_validator("parity_tolerance_pct")
    @classmethod
    def check_parity_tolerance_range(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError(f"competitiveness_frontier.parity_tolerance_pct must be in (0, 1), got {v}")
        return v


# --------------------------------------------------------------------------
# Top-level scenario config
# --------------------------------------------------------------------------

class ScenarioConfig(BaseModel):
    regions: RegionsConfig
    technologies: TechnologiesConfig
    electrolyzer: ElectrolyzerConfig
    renewables: RenewablesConfig
    topsis: TopsisConfig
    thresholds: ThresholdsConfig
    sensitivity: SensitivityConfig
    competitiveness_frontier: CompetitivenessFrontierConfig


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path(__file__).parent / "scenario_params.yaml"


def load_scenario_config(path: Optional[Path] = None) -> ScenarioConfig:
    """
    Load and validate scenario_params.yaml into a ScenarioConfig object.

    Parameters
    ----------
    path : Optional[Path]
        Path to the YAML file. Defaults to scenario_params.yaml in the
        same directory as this module.

    Returns
    -------
    ScenarioConfig
        Fully validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the YAML file does not exist.
    pydantic.ValidationError
        If the YAML content fails schema or cross-field validation.
    """
    config_path = path if path is not None else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Scenario config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    return ScenarioConfig(**raw_data)