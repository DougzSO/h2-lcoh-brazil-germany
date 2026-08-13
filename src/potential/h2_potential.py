"""Technical hydrogen production potential per candidate site (METHODOLOGY.md
2.4): for each candidate hydrogen-hub site retained by
spatial/site_selection.py, computes installable capacity (MW), annual
electricity yield (MWh), and annual hydrogen output (kg/yr and t/yr). This
is the bridge module between the spatial pipeline (a GeoDataFrame of ranked
candidate sites) and the economics layer (per-site LCOH inputs) -- neither
side requires any change to consume this module's output.

INPUT NOTE: reads candidate_sites_{region}_{tech}_baseline.geojson directly
from data/processed/ (already written by
site_selection.select_candidate_sites() -- Hard Rule 7, root CLAUDE.md:
"Each stage writes to disk before next stage reads."), rather than
re-running site selection. site_selection.py's logic is not imported or
modified. Always reads the "_baseline" file regardless of THIS module's own
`scenario` parameter -- site selection is scenario-invariant (see
site_selection.py's own FILENAME NOTE), so there is no
"_min"/"_max"-suffixed candidate-sites file to read.

PARAMETER NOTE: capacity density, full-load hours, and capacity factor are
ALL resolved via economics.decomposition._renewable_capacity_density() /
_renewable_full_load_hours() / _renewable_capacity_factor(config, region,
renewable_tech, scenario) -- reused directly rather than re-implemented, so
this module's technical H2 potential and decomposition.py's
spatial-aggregated LCOH can no longer silently diverge onto two different
capacity factors (or capacity densities) for the same region+tech+scenario.
Each resolves a literature-validated {min, baseline, max} range from
config.technologies.<renewable_tech>.<region> at the COMBINED scenario
branch (`scenario` parameter below, default "baseline" -- see
docs/memory/09_methodology_assumptions.md for capacity factor,
docs/memory/10_capacity_density_assumptions.md for capacity density).
Both are region-specific in this schema. Specific energy consumption is
read from config.electrolyzer.technologies[electrolyzer_type].efficiency_kwh_per_kg.
No numeric parameter is hardcoded here.

CRS ROUND-TRIP NOTE: GDAL's GeoJSON driver tags the written CRS as
EPSG:4326 per RFC 7946, without actually reprojecting this pipeline's
AEA-meter coordinate values -- verified live (write a GeoDataFrame in the
AEA CRS, read it back: reported crs is EPSG:4326 but total_bounds are
still the original meter values). calculate_h2_potential() therefore
overrides the CRS read back from candidate_sites_{region}_{tech}.geojson
with RegionCRS.projected_crs_for(region) via set_crs(allow_override=True)
rather than reprojecting via to_crs() -- the latter would treat raw AEA
meters as if they were degrees and produce Inf/NaN coordinates. This
affects any consumer of site_selection.py's GeoJSON output, not something
introduced by this module.

OUTPUT PATH NOTE: outputs are written to
data/processed/h2_potential_{region}_{tech}_{scenario}.{geojson,csv} --
the filename encodes the combined scenario branch ("min"/"baseline"/"max")
but not electrolyzer_type, so re-running calculate_h2_potential() for the
same region/tech/scenario with a different electrolyzer_type overwrites the
previous file. This mirrors the existing PEM-baseline/alkaline-sensitivity
asymmetry documented in docs/memory/05_economic_model.md: PEM is the
technology used for on-disk baseline output, alkaline is a sensitivity-only
variant whose GeoDataFrame return value callers (e.g. a future
sensitivity/sensitivity_analysis.py extension) can consume in-memory
without relying on the written file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Tuple

import geopandas as gpd

from src.core.constants import Region, RegionCRS
from src.economics.decomposition import (
    _renewable_capacity_density,
    _renewable_capacity_factor,
    _renewable_full_load_hours,
)

PROCESSED_DIR = Path("data/processed")

# tech ("solar"/"wind", this module's + site_selection.py's siting
# vocabulary) -> renewable_tech ("solar_pv"/"onshore_wind",
# economics/decomposition.py's vocabulary) -- same mapping
# decomposition.py's own _TECH_TO_RENEWABLE_TECH applies, needed here only
# to call its resolver functions with the right key.
_TECH_TO_RENEWABLE_TECH = {"solar": "solar_pv", "wind": "onshore_wind"}

OUTPUT_COLUMNS = [
    "rank", "site_id", "region", "tech", "electrolyzer_type",
    "mean_suitability", "max_suitability", "suitable_area_km2",
    "installable_capacity_mw", "annual_electricity_yield_mwh",
    "annual_h2_production_kg", "annual_h2_production_t", "geometry",
]

_KWH_PER_MWH = 1000.0
_KG_PER_TONNE = 1000.0


class H2PotentialError(RuntimeError):
    """Raised when technical H2 potential cannot be computed for a region/tech pair."""


def _empty_gdf(target_crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {c: [] for c in OUTPUT_COLUMNS if c != "geometry"},
        geometry=gpd.GeoSeries([], crs=target_crs), crs=target_crs,
    )[OUTPUT_COLUMNS]


def _resolve_parameters(
    region: Region, tech: str, config, electrolyzer_type: str, scenario: str
) -> Tuple[float, float, float, float]:
    """Resolve (power_density_mw_per_km2, full_load_hours,
    specific_consumption_kwh_per_kg, capacity_factor) from ScenarioConfig
    -- never hardcoded (Hard Rule 4, root CLAUDE.md).

    All three renewable-side values are resolved via
    economics.decomposition's shared resolver functions at the given
    combined `scenario` branch, reused directly rather than re-implemented
    (see module PARAMETER NOTE) so this module's own technical H2 potential
    and decomposition.py's spatial-aggregated LCOH always agree on the same
    region-specific capacity factor and capacity density for a given
    scenario.
    """
    if electrolyzer_type not in config.electrolyzer.technologies:
        raise ValueError(
            f"electrolyzer_type must be one of "
            f"{sorted(config.electrolyzer.technologies)}, got {electrolyzer_type!r}"
        )
    renewable_tech = _TECH_TO_RENEWABLE_TECH[tech]

    power_density_mw_per_km2 = _renewable_capacity_density(config, region, renewable_tech, scenario=scenario)
    full_load_hours = _renewable_full_load_hours(config, region, renewable_tech, scenario=scenario)
    capacity_factor = _renewable_capacity_factor(config, region, renewable_tech, scenario=scenario)

    specific_consumption_kwh_per_kg = (
        config.electrolyzer.technologies[electrolyzer_type].efficiency_kwh_per_kg
    )
    return power_density_mw_per_km2, full_load_hours, specific_consumption_kwh_per_kg, capacity_factor


def calculate_h2_potential(
    region: Region, tech: str, config, electrolyzer_type: str = "pem", scenario: str = "baseline"
) -> gpd.GeoDataFrame:
    """Compute technical hydrogen potential for each candidate site of a
    region/tech pair, for a given electrolyzer technology and COMBINED
    scenario branch.

    Parameters
    ----------
    region : Region
    tech : str
        "solar" or "wind" -- must match a previously run
        site_selection.select_candidate_sites(region, tech, config) output
        on disk (the scenario-invariant "_baseline"-suffixed file -- see
        module INPUT NOTE).
    config : ScenarioConfig
        config.technologies.<solar_pv|onshore_wind>.<brazil|germany> and
        config.electrolyzer.technologies[electrolyzer_type] are read --
        never hardcoded (Hard Rule 4, root CLAUDE.md).
    electrolyzer_type : str
        "pem" (default, baseline) or "alkaline" (sensitivity variant) --
        must be a key present in config.electrolyzer.technologies.
    scenario : str
        "min"/"baseline"(default)/"max" -- the combined scenario branch
        capacity_factor and capacity_density are resolved at (see module
        PARAMETER NOTE). Encoded in the output filename.

    Returns
    -------
    gpd.GeoDataFrame
        site_selection.py's columns plus electrolyzer_type,
        installable_capacity_mw, annual_electricity_yield_mwh,
        annual_h2_production_kg, annual_h2_production_t. geometry is never
        dropped. Empty (0 rows, correct columns/CRS) if the candidate-sites
        input has 0 rows.
    """
    if tech not in ("solar", "wind"):
        raise ValueError(f"tech must be 'solar' or 'wind', got {tech!r}")

    t0 = time.time()
    print(
        f"[h2_potential] {region.value}/{tech}/{electrolyzer_type}/{scenario}: "
        f"computing technical H2 potential"
    )

    target_crs = RegionCRS.projected_crs_for(region)
    power_density_mw_per_km2, full_load_hours, specific_consumption_kwh_per_kg, capacity_factor = (
        _resolve_parameters(region, tech, config, electrolyzer_type, scenario)
    )
    print(
        f"  resolved capacity_factor={capacity_factor:.3f} "
        f"(full_load_hours={full_load_hours:.0f} h/yr)"
    )

    sites_path = PROCESSED_DIR / f"candidate_sites_{region.value}_{tech}_baseline.geojson"
    if not sites_path.exists():
        raise FileNotFoundError(
            f"Candidate sites file not found: {sites_path}. Run "
            f"src.spatial.site_selection.select_candidate_sites({region.value!r}, "
            f"{tech!r}, config) first."
        )
    sites = gpd.read_file(sites_path)

    if sites.empty:
        print(f"[h2_potential] {region.value}/{tech}: 0 candidate sites, writing empty output")
        result = _empty_gdf(target_crs)
    else:
        if str(sites.crs) != str(target_crs):
            # GDAL's GeoJSON driver tags the CRS as EPSG:4326 per RFC 7946
            # on write, WITHOUT actually reprojecting site_selection.py's
            # raw AEA-meter coordinate values (verified live: writing a
            # GeoDataFrame in the AEA CRS and reading it back reports
            # sites.crs == EPSG:4326 while total_bounds are still the
            # original meter values, e.g. [0, 0, 10000, 10000]). Calling
            # .to_crs() here would treat those raw meters as degrees and
            # produce Inf/NaN coordinates. RegionCRS.projected_crs_for()
            # is this pipeline's authoritative CRS (Hard Rule 1, root
            # CLAUDE.md), so overriding the file's mislabeled tag is
            # correct here, not a workaround -- see the module docstring.
            sites = sites.set_crs(target_crs, allow_override=True)

        installable_capacity_mw = sites["suitable_area_km2"] * power_density_mw_per_km2
        annual_electricity_yield_mwh = installable_capacity_mw * full_load_hours
        annual_h2_production_kg = (
            annual_electricity_yield_mwh * _KWH_PER_MWH / specific_consumption_kwh_per_kg
        )
        annual_h2_production_t = annual_h2_production_kg / _KG_PER_TONNE

        result = sites.copy()
        result["electrolyzer_type"] = electrolyzer_type
        result["installable_capacity_mw"] = installable_capacity_mw
        result["annual_electricity_yield_mwh"] = annual_electricity_yield_mwh
        result["annual_h2_production_kg"] = annual_h2_production_kg
        result["annual_h2_production_t"] = annual_h2_production_t
        result = result[OUTPUT_COLUMNS]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path = PROCESSED_DIR / f"h2_potential_{region.value}_{tech}_{scenario}.geojson"
    csv_path = PROCESSED_DIR / f"h2_potential_{region.value}_{tech}_{scenario}.csv"

    result.to_file(geojson_path, driver="GeoJSON")
    csv_df = result.copy()
    csv_df["geometry_wkt"] = csv_df["geometry"].apply(lambda g: g.wkt if g is not None else None)
    csv_df.drop(columns="geometry").to_csv(csv_path, index=False)

    if not result.empty:
        total_mw = result["installable_capacity_mw"].sum()
        total_t = result["annual_h2_production_t"].sum()
        top = result.iloc[0]
        print(
            f"  total installable capacity: {total_mw:.1f} MW across {len(result)} site(s)"
        )
        print(f"  total annual H2 production: {total_t:.1f} t/yr")
        print(
            f"  top site: {top['site_id']} (installable_capacity_mw="
            f"{top['installable_capacity_mw']:.1f}, annual_h2_production_t="
            f"{top['annual_h2_production_t']:.1f})"
        )

    print(
        f"[h2_potential] {region.value}/{tech}/{electrolyzer_type}/{scenario}: "
        f"{len(result)} site(s) -> {geojson_path}, {csv_path} in {time.time() - t0:.1f}s"
    )
    return result


def run_all_potential(
    config, electrolyzer_type: str = "pem"
) -> Dict[str, Dict[str, Dict[str, gpd.GeoDataFrame]]]:
    """Run calculate_h2_potential() for both regions, both siting
    technologies, and all three combined scenarios (min/baseline/max) at a
    single electrolyzer technology.

    Returns
    -------
    Dict[str, Dict[str, Dict[str, gpd.GeoDataFrame]]]
        Keyed by region.value, then by tech ("solar"/"wind"), then by
        scenario ("min"/"baseline"/"max").
    """
    results: Dict[str, Dict[str, Dict[str, gpd.GeoDataFrame]]] = {}
    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        results[region.value] = {}
        for tech in ("solar", "wind"):
            results[region.value][tech] = {}
            for scenario in ("min", "baseline", "max"):
                results[region.value][tech][scenario] = calculate_h2_potential(
                    region, tech, config, electrolyzer_type, scenario=scenario
                )
    return results
