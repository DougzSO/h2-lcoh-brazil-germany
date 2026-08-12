"""
Shared configuration access patterns used across spatial modules.

Consolidates the config.regions.<region>.grid_crs lookup in one place so
callers don't repeat attribute-path knowledge about ScenarioConfig's shape.
"""

from src.core.constants import Region


def get_grid_crs(region: Region, config) -> str:
    """
    Fetch a region's grid CRS from a validated ScenarioConfig.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.
    config : ScenarioConfig
        Must expose config.regions.<region.value>.grid_crs (guaranteed by
        Pydantic validation at load time via config_loader.load_scenario_config).

    Returns
    -------
    str
        CRS definition for the region (a PROJ4 string; see RegionCRS).

    Raises
    ------
    AttributeError
        If config does not expose the expected structure. This should only
        happen if a caller passes a malformed or wrong-type object instead
        of a real ScenarioConfig -- Pydantic validation at load time
        guarantees the field exists for any config that came from
        load_scenario_config().
    """
    region_config = getattr(config.regions, region.value)
    return region_config.grid_crs


def get_protected_area_buffer_km(region: Region, config) -> float:
    """
    Fetch a region's protected-area exclusion buffer distance (km) from a
    validated ScenarioConfig.

    Region-specific, not a shared scalar (ADR-011, docs/memory/
    06_technical_decisions_log.md): Brazil's federal protected areas are
    strict no-use reserves, while Germany's dataset (Natura 2000) permits
    sustainable renewable-energy development under EU law.

    Parameters
    ----------
    region : Region
        Region.NORDESTE_BR or Region.NORTH_GERMANY.
    config : ScenarioConfig
        Must expose config.thresholds.min_distance_to_protected_area_km.<region.value>
        (guaranteed by Pydantic validation at load time).

    Returns
    -------
    float
        Buffer distance in kilometres.
    """
    return getattr(config.thresholds.min_distance_to_protected_area_km, region.value)