"""
Central constants module defining regions, CRS definitions, and configuration
structures shared across the spatial and economics analysis.

CRS DESIGN NOTE:
    Analysis grids use custom Albers Equal Area Conic (AEA) projections,
    one per region, rather than UTM. UTM is conformal (preserves shape/angle),
    not equal-area (preserves area) -- at the scale of these study regions
    (Northeast Brazil spans ~1500 km across multiple UTM zones; North Germany
    spans two full Bundesländer), UTM's area distortion away from the zone's
    central meridian is not negligible and would silently bias any
    area-dependent computation (zonal statistics, installable capacity =
    area x power density, LCOH per site). AEA guarantees area fidelity
    everywhere within the projection's extent, at the cost of some angular
    distortion -- acceptable here since no analysis in this pipeline depends
    on preserved angles/shape.

    Standard parallels and central meridian/latitude below were chosen to
    bracket each region's real geographic extent (see admin_boundaries.py
    for the definitive region membership).
"""

from enum import Enum
from typing import Dict


class Region(str, Enum):
    """Enumeration of study regions."""
    NORDESTE_BR = "brazil"
    NORTH_GERMANY = "germany"


class RegionCRS:
    """Map of regions to their projected, equal-area CRS (in meters, for
    analysis grids). Defined as PROJ4 strings (custom Albers Equal Area
    Conic) rather than EPSG codes, since no standard EPSG equal-area CRS
    is centered appropriately on either study region.
    """

    # Custom Albers Equal Area Conic, centered on Northeast Brazil
    # (9 states: MA, PI, CE, RN, PB, PE, AL, SE, BA).
    NORDESTE_BR_PROJECTED = (
        "+proj=aea +lat_1=-5 +lat_2=-15 +lat_0=-10 +lon_0=-40 "
        "+x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs"
    )

    # Custom Albers Equal Area Conic, centered on North Germany
    # (Schleswig-Holstein + Niedersachsen, full states, GADM level 1).
    NORTH_GERMANY_PROJECTED = (
        "+proj=aea +lat_1=53 +lat_2=54.5 +lat_0=53.75 +lon_0=9.5 "
        "+x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs"
    )

    # Geographic (degree-based) CRS for source data.
    GEOGRAPHIC_WGS84 = "EPSG:4326"

    # Mapping region to its projected CRS.
    _REGION_CRS_MAP: Dict[Region, str] = {
        Region.NORDESTE_BR: NORDESTE_BR_PROJECTED,
        Region.NORTH_GERMANY: NORTH_GERMANY_PROJECTED,
    }

    @classmethod
    def projected_crs_for(cls, region: Region) -> str:
        """
        Return the projected, equal-area CRS definition for a given region.

        Parameters
        ----------
        region : Region
            The study region.

        Returns
        -------
        str
            PROJ4 string defining a custom Albers Equal Area Conic
            projection centered on the region.

        Raises
        ------
        ValueError
            If region is not recognized.
        """
        if region not in cls._REGION_CRS_MAP:
            raise ValueError(f"Unknown region: {region}")
        return cls._REGION_CRS_MAP[region]