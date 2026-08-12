"""Queries OpenStreetMap's public Overpass API for major inland water
features (natural=water, waterway=riverbank, water=reservoir, waterway=river)
within a region's bounding box, rasterizes them onto the region's 1km
analysis grid, computes a Euclidean distance-to-water raster in meters, and
writes it to data/raw/water_bodies/<region>/distance_to_water.tif.
spatial/data_layers.py's load_distance_to_water() reads the file this module
writes."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString, Polygon
import geopandas as gpd

from src.core.constants import Region, RegionCRS
from src.spatial import admin_boundaries
from src.spatial.grid_utils import get_analysis_grid

RAW_DIR = Path("data/raw/water_bodies")

# Primary + fallback public Overpass endpoints (same query language/API
# contract; each fallback is tried only if every previous one times out or
# errors on every attempt against it). maps.mail.ru added as a third mirror
# since overpass-api.de/overpass.kumi.systems alone were observed returning
# HTTP 504s under load for whole-state-sized bboxes.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
HEADERS = {"User-Agent": "h2-lcoh-pipeline/1.0 (research use)"}

# Server-side query timeout (seconds) and client-side HTTP timeout, kept
# generous since these bboxes span whole states/regions. Deliberately
# excludes minor streams/ditches/canals -- only tags that plausibly matter
# for siting exclusion (see METHODOLOGY.md 2.3) are queried, to avoid
# overloading Overpass with the much larger minor-waterway dataset.
QUERY_TIMEOUT_S = 180
HTTP_TIMEOUT_S = QUERY_TIMEOUT_S + 30
MAX_ATTEMPTS_PER_SERVER = 2

# Combines waterway=river/riverbank into a single regex-filtered clause
# (fewer distinct filter statements = a cheaper query plan server-side,
# reducing 504 risk on whole-state bboxes) while keeping water=reservoir
# as its own clause -- material for Northeast Brazil, where large açude
# reservoirs are a significant fraction of inland water area (METHODOLOGY.md
# 2.3) and would otherwise be dropped.
_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  way["waterway"~"river|riverbank"]({bbox});
  way["natural"="water"]({bbox});
  way["water"="reservoir"]({bbox});
  relation["natural"="water"]({bbox});
);
out geom;
"""


class WaterBodiesFetchError(RuntimeError):
    """Raised when OSM water features cannot be downloaded or rasterized."""


def _query_overpass(bbox_wgs84: tuple[float, float, float, float]) -> dict:
    """POST the Overpass QL query for `bbox_wgs84` (minx, miny, maxx, maxy),
    retrying each endpoint on timeout/connection error before falling back
    to the next one. Raises WaterBodiesFetchError only after every endpoint
    has been exhausted."""
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
    raise WaterBodiesFetchError(
        f"Overpass query failed on all endpoints {OVERPASS_URLS}: {last_exc}"
    )


def _way_geometry(coords: list[tuple[float, float]], tags: dict) -> LineString | Polygon | None:
    """Build a shapely geometry from a way's (lon, lat) coordinate list.
    Closed ways (first == last point) with a plausible ring (>=4 points)
    are treated as polygons (lakes/reservoirs/riverbanks); everything else
    is a line (river centerlines). `tags` is accepted for readability at
    call sites but is not currently used to override this shape inference."""
    if len(coords) < 2:
        return None
    if len(coords) >= 4 and coords[0] == coords[-1]:
        try:
            return Polygon(coords)
        except Exception:
            return None
    return LineString(coords)


def _elements_to_geometries(elements: list[dict]) -> list:
    """Convert Overpass 'out geom' way/relation elements into shapely
    geometries in EPSG:4326. Relation members are flattened into their
    individual way geometries (inner/outer ring roles are not
    distinguished -- acceptable for a distance-to-water raster, which only
    needs to know where water broadly is, not exact lake outlines)."""
    geoms = []
    for el in elements:
        el_type = el.get("type")
        if el_type == "way" and "geometry" in el:
            coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
            geom = _way_geometry(coords, el.get("tags", {}))
            if geom is not None:
                geoms.append(geom)
        elif el_type == "relation":
            for member in el.get("members", []):
                if member.get("type") == "way" and "geometry" in member:
                    coords = [(pt["lon"], pt["lat"]) for pt in member["geometry"]]
                    geom = _way_geometry(coords, el.get("tags", {}))
                    if geom is not None:
                        geoms.append(geom)
    return geoms


def fetch(region: Region, config) -> Path:
    """Query Overpass for major water features covering `region`'s bounding
    box, rasterize them onto the region's 1km analysis grid, compute a
    Euclidean distance-to-water raster in meters, and write
    data/raw/water_bodies/<region>/distance_to_water.tif. Returns the Path.
    """
    out_path = RAW_DIR / region.value / "distance_to_water.tif"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[water_bodies] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    t0 = time.time()
    print(f"[water_bodies] {region.value}: OSM Overpass API (major inland water features)")

    bbox_wgs84 = admin_boundaries.get_region_bounds_wgs84(region)
    osm_data = _query_overpass(bbox_wgs84)

    elements = osm_data.get("elements", [])
    geoms_4326 = _elements_to_geometries(elements)
    print(f"  parsed {len(geoms_4326)} water features from {len(elements)} OSM elements")
    if not geoms_4326:
        raise WaterBodiesFetchError(
            f"No water features parsed for region {region.value!r} -- check the "
            f"Overpass query/bbox before proceeding (an empty result silently "
            f"produces a meaningless all-far distance raster)."
        )

    target_crs = RegionCRS.projected_crs_for(region)
    geoms_projected = gpd.GeoSeries(geoms_4326, crs="EPSG:4326").to_crs(target_crs)

    grid_array, transform = get_analysis_grid(region, config)
    out_shape = grid_array.shape

    print(f"  rasterizing onto {out_shape} grid...", end="", flush=True)
    t_raster = time.time()
    water_mask = rasterize(
        [(geom, 1) for geom in geoms_projected if geom is not None and not geom.is_empty],
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="uint8",
    )
    print(f" done in {time.time() - t_raster:.1f}s ({int(water_mask.sum())} water cells)")

    pixel_w = abs(transform.a)
    pixel_h = abs(transform.e)
    print(f"  computing Euclidean distance transform ({pixel_w:.0f}m x {pixel_h:.0f}m cells)...",
          end="", flush=True)
    t_dist = time.time()
    distance_m = distance_transform_edt(
        ~water_mask.astype(bool), sampling=(pixel_h, pixel_w)
    ).astype(np.float32)
    print(f" done in {time.time() - t_dist:.1f}s "
          f"(max distance {distance_m.max():,.0f}m)")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Euclidean distance is never negative, so -9999 is a safe nodata
    # sentinel here: distance_transform_edt() populates every grid cell
    # with a real, finite value (including 0.0 for cells on a water
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
    print(f"[water_bodies] {region.value}: wrote {out_path} ({size_mb:.2f} MB) "
          f"in {time.time() - t0:.1f}s")
    return out_path
