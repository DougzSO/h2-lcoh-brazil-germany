"""Queries OpenStreetMap's public Overpass API for power transmission
infrastructure (power=line, power=cable, power=substation) within a
region's bounding box, rasterizes it onto the region's 1km analysis grid,
computes a Euclidean distance-to-grid raster in meters, and writes it to
data/raw/grid/<region>/distance_to_grid.tif.
spatial/data_layers.py's load_distance_to_grid() reads the file this module
writes.

SOURCE NOTE: ARCHITECTURE.md / docs/memory/03_data_sources_and_acquisition.md
describe this layer as sourced from ANEEL SIGA (Brazil) and
Marktstammdatenregister (Germany), two country-specific official APIs with
divergent schemas and access mechanics. This module instead follows the
water_bodies_fetch.py pattern -- a single OSM Overpass query -- so both
regions share one acquisition/rasterization/distance-transform code path,
consistent with the "no heavy external dependencies" constraint on this
layer. See SPRINT_LOG.md and 03_data_sources_and_acquisition.md for the
resulting documentation update.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString, Point, Polygon
import geopandas as gpd

from src.core.constants import Region, RegionCRS
from src.spatial import admin_boundaries
from src.spatial.grid_utils import get_analysis_grid

RAW_DIR = Path("data/raw/grid")

# Primary + fallback public Overpass endpoints, same contract (and same
# 504-driven third mirror) as water_bodies_fetch.py.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
HEADERS = {"User-Agent": "h2-lcoh-pipeline/1.0 (research use)"}

QUERY_TIMEOUT_S = 180
HTTP_TIMEOUT_S = QUERY_TIMEOUT_S + 30
MAX_ATTEMPTS_PER_SERVER = 2

# Combines power=line/cable into a single regex-filtered clause (fewer
# distinct filter statements = a cheaper query plan server-side); the rare
# relation-typed substation is dropped for the same reason (substations are
# overwhelmingly mapped as nodes or ways in OSM), but both node and way
# substation forms are kept since dropping node substations would miss the
# majority of real-world substation mappings.
_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  way["power"~"line|cable"]({bbox});
  node["power"="substation"]({bbox});
  way["power"="substation"]({bbox});
);
out geom;
"""


class GridInfrastructureFetchError(RuntimeError):
    """Raised when OSM power infrastructure cannot be downloaded or rasterized."""


def _query_overpass(bbox_wgs84: tuple[float, float, float, float]) -> dict:
    """POST the Overpass QL query for `bbox_wgs84` (minx, miny, maxx, maxy),
    retrying each endpoint on timeout/connection error before falling back
    to the next one. Raises GridInfrastructureFetchError only after every
    endpoint has been exhausted."""
    minx, miny, maxx, maxy = bbox_wgs84
    overpass_bbox = f"{miny},{minx},{maxy},{maxx}"
    query = _QUERY_TEMPLATE.format(timeout=QUERY_TIMEOUT_S, bbox=overpass_bbox)

    last_exc: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(1, MAX_ATTEMPTS_PER_SERVER + 1):
            try:
                print(f"  querying {url} (attempt {attempt}/{MAX_ATTEMPTS_PER_SERVER})...",
                      end="", flush=True)
                t0 = time.time()
                resp = requests.post(url, data={"data": query}, headers=HEADERS,
                                      timeout=HTTP_TIMEOUT_S)
                resp.raise_for_status()
                data = resp.json()
                print(f" ok in {time.time() - t0:.1f}s")
                return data
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                    ValueError) as exc:
                last_exc = exc
                print(f" failed ({exc})")
    raise GridInfrastructureFetchError(
        f"Overpass query failed on all endpoints {OVERPASS_URLS}: {last_exc}"
    )


def _way_geometry(coords: list[tuple[float, float]]) -> LineString | Polygon | None:
    """Build a shapely geometry from a way's (lon, lat) coordinate list.
    Closed ways (first == last point) with a plausible ring (>=4 points)
    are treated as polygons (substation compounds mapped as an area);
    everything else is a line (transmission line/cable centerlines)."""
    if len(coords) < 2:
        return None
    if len(coords) >= 4 and coords[0] == coords[-1]:
        try:
            return Polygon(coords)
        except Exception:
            return None
    return LineString(coords)


def _elements_to_geometries(elements: list[dict]) -> list:
    """Convert Overpass 'out geom' node/way/relation elements into shapely
    geometries in EPSG:4326. Substation nodes become Points; lines/cables
    and substation ways become Line/Polygon via _way_geometry(). Relation
    members are flattened into their individual way geometries (inner/outer
    ring roles are not distinguished -- acceptable for a distance-to-grid
    raster, which only needs to know where infrastructure broadly is)."""
    geoms = []
    for el in elements:
        el_type = el.get("type")
        if el_type == "node" and "lon" in el and "lat" in el:
            geoms.append(Point(el["lon"], el["lat"]))
        elif el_type == "way" and "geometry" in el:
            coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
            geom = _way_geometry(coords)
            if geom is not None:
                geoms.append(geom)
        elif el_type == "relation":
            for member in el.get("members", []):
                if member.get("type") == "way" and "geometry" in member:
                    coords = [(pt["lon"], pt["lat"]) for pt in member["geometry"]]
                    geom = _way_geometry(coords)
                    if geom is not None:
                        geoms.append(geom)
                elif member.get("type") == "node" and "lon" in member and "lat" in member:
                    geoms.append(Point(member["lon"], member["lat"]))
    return geoms


def fetch(region: Region, config) -> Path:
    """Query Overpass for power transmission infrastructure (power=line,
    power=cable, power=substation) covering `region`'s bounding box,
    rasterize it onto the region's 1km analysis grid, compute a Euclidean
    distance-to-grid raster in meters, and write
    data/raw/grid/<region>/distance_to_grid.tif. Returns the Path.
    """
    out_path = RAW_DIR / region.value / "distance_to_grid.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[grid_infrastructure] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    print(f"[grid_infrastructure] {region.value}: OSM Overpass API (power transmission infrastructure)")

    bbox_wgs84 = admin_boundaries.get_region_bounds_wgs84(region)
    osm_data = _query_overpass(bbox_wgs84)

    elements = osm_data.get("elements", [])
    geoms_4326 = _elements_to_geometries(elements)
    print(f"  parsed {len(geoms_4326)} grid features from {len(elements)} OSM elements")
    if not geoms_4326:
        raise GridInfrastructureFetchError(
            f"No power infrastructure features parsed for region {region.value!r} -- "
            f"check the Overpass query/bbox before proceeding (an empty result "
            f"silently produces a meaningless all-far distance raster)."
        )

    target_crs = RegionCRS.projected_crs_for(region)
    geoms_projected = gpd.GeoSeries(geoms_4326, crs="EPSG:4326").to_crs(target_crs)

    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape

    print(f"  rasterizing onto {out_shape} grid...", end="", flush=True)
    t_raster = time.time()
    grid_mask = rasterize(
        [(geom, 1) for geom in geoms_projected if geom is not None and not geom.is_empty],
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="uint8",
    )
    print(f" done in {time.time() - t_raster:.1f}s ({int(grid_mask.sum())} grid cells)")

    pixel_w = abs(transform.a)
    pixel_h = abs(transform.e)
    print(f"  computing Euclidean distance transform ({pixel_w:.0f}m x {pixel_h:.0f}m cells)...",
          end="", flush=True)
    t_dist = time.time()
    distance_m = distance_transform_edt(
        ~grid_mask.astype(bool), sampling=(pixel_h, pixel_w)
    ).astype(np.float32)
    print(f" done in {time.time() - t_dist:.1f}s "
          f"(max distance {distance_m.max():,.0f}m)")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Euclidean distance is never negative, so -9999 is a safe nodata
    # sentinel here: distance_transform_edt() populates every grid cell
    # with a real, finite value (including 0.0 for cells on a grid
    # feature itself), so -9999 will never collide with legitimate data.
    # Without an explicit nodata tag, grid_utils.reproject_and_resample()
    # falls back to src.nodata (unset) and warns that bilinear/cubic
    # resampling proceeds unmasked -- harmless today since this raster
    # has no actual missing pixels, but leaving nodata unset is still an
    # unnecessary landmine for any future change to this fetch (e.g.
    # clipping to the dissolved region polygon instead of its bbox).
    profile = {
        "driver": "GTiff",
        "height": distance_m.shape[0],
        "width": distance_m.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": target_crs,
        "transform": transform,
        "nodata": -9999.0,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(distance_m, 1)

    size_mb = out_path.stat().st_size / 1e6
    print(f"[grid_infrastructure] {region.value}: wrote {out_path} ({size_mb:.2f} MB) "
          f"in {time.time() - t0:.1f}s")
    return out_path
