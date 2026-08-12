"""Queries official protected-area datasets for both study regions: ICMBio/
MMA conservation units (Brazil, via INDE's public GeoServer WFS) and Natura
2000 protected sites (Germany, via the EEA discomap ArcGIS REST service),
each queried with a server-side bounding-box filter and written directly as
data/raw/protected_areas/<region>/protected_areas.geojson.

spatial/exclusion_mask.py's rasterization step reads the file this module
writes (alongside landuse_fetch.py's output) -- together these are the two
inputs needed for the binary suitable/excluded mask (METHODOLOGY.md 2.3).
Both queries return GeoJSON directly from the server, so this module needs
no shapefile/GML parsing dependency beyond `requests`.

SOURCE NOTE (Brazil): METHODOLOGY.md / ARCHITECTURE.md describe this layer
as "ICMBio/MMA ... Automated direct shapefile download". Rather than
downloading and unpacking a national shapefile ZIP locally (which would add
a fiona/zipfile dependency for what is otherwise a one-off bbox crop), this
module queries INDE's (Infraestrutura Nacional de Dados Espaciais, Brazil's
federal spatial data infrastructure) public GeoServer WFS for the MMA/ICMBio
conservation-units layer directly, with a server-side BBOX filter and
outputFormat=application/json. The exact layer typeName below
(INDE_LAYER_TYPENAME) is a best-effort identifier from published INDE
catalog documentation, not verified against a live response -- no network
access was available when this module was written. Confirm before the
first real run.

FALLBACK NOTE (Brazil): a live run surfaced an HTTP 400 from the INDE WFS,
traced to the bbox filter's axis order -- GeoServer's WFS 2.0.0
implementation honors the axis order declared by an explicit URN CRS
(EPSG:4326's URN form is lat,lon), which does not match the lon,lat
("minx,miny,maxx,maxy") order this module previously sent with a bare
"EPSG:4326" suffix. Fixed to send the WFS-2.0-compliant
"miny,minx,maxy,maxx,urn:ogc:def:crs:EPSG::4326" form. If the INDE WFS
request still fails for any reason (this endpoint's exact behavior
remains otherwise unverified against a live response), fetch_brazil()
falls back to querying OSM's Overpass API directly for
boundary=protected_area / boundary=national_park / leisure=nature_reserve
ways and relations -- the same public-mirror resilience pattern already
used by water_bodies_fetch.py / grid_infrastructure_fetch.py -- so the
pipeline stays runnable regardless of INDE's own reliability.

SOURCE NOTE (Germany): queries the EEA discomap ArcGIS REST FeatureServer
for the Natura 2000 dynamic dataset directly (f=geojson), rather than the
raw OGC WFS/GML endpoint, since the ArcGIS REST "query" operation returns
GeoJSON natively. Endpoint and layer index are a best-effort identifier
from published EEA discomap documentation, not verified against a live
response -- confirm before the first real run. This is the same class of
documented assumption already flagged for landuse_fetch.py's Corine WCS
endpoint and grid_infrastructure_fetch.py's OSM-vs-ANEEL/MaStR deviation
(see SPRINT_LOG.md).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from shapely.geometry import Polygon, mapping

from src.core.constants import Region
from src.spatial import admin_boundaries

RAW_DIR = Path("data/raw/protected_areas")

# Public Overpass mirrors + resilience settings, matching
# water_bodies_fetch.py / grid_infrastructure_fetch.py's own convention --
# used only as fetch_brazil()'s fallback when the INDE WFS query fails
# (see FALLBACK NOTE on fetch_brazil() below).
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_HEADERS = {"User-Agent": "h2-lcoh-pipeline/1.0 (research use)"}
OVERPASS_QUERY_TIMEOUT_S = 180
OVERPASS_HTTP_TIMEOUT_S = OVERPASS_QUERY_TIMEOUT_S + 30
OVERPASS_MAX_ATTEMPTS_PER_SERVER = 2

_OVERPASS_PROTECTED_AREAS_QUERY = """
[out:json][timeout:{timeout}];
(
  way["boundary"="protected_area"]({bbox});
  way["boundary"="national_park"]({bbox});
  way["leisure"="nature_reserve"]({bbox});
  relation["boundary"="protected_area"]({bbox});
  relation["boundary"="national_park"]({bbox});
  relation["leisure"="nature_reserve"]({bbox});
);
out geom;
"""

# INDE (Infraestrutura Nacional de Dados Espaciais) public GeoServer WFS,
# hosting the MMA/ICMBio Cadastro Nacional de Unidades de Conservacao (CNUC)
# layer. See SOURCE NOTE (Brazil) above.
INDE_WFS_BASE = "https://geoservicos.inde.gov.br/geoserver/MMA/wfs"
INDE_LAYER_TYPENAME = "MMA:cnuc_uc"

# EEA discomap ArcGIS REST service for the Natura 2000 dynamic dataset.
# See SOURCE NOTE (Germany) above.
NATURA2000_QUERY_URL = (
    "https://bio.discomap.eea.europa.eu/arcgis/rest/services/"
    "ProtectedSites/Natura2000_Dyna_WM/MapServer/0/query"
)

HTTP_TIMEOUT_S = 120
HEADERS = {"User-Agent": "h2-lcoh-pipeline/1.0 (research use)"}


class ProtectedAreasFetchError(RuntimeError):
    """Raised when protected-area features cannot be downloaded or parsed."""


def _region_bbox_wgs84(region: Region) -> tuple[float, float, float, float]:
    return admin_boundaries.get_region_bounds_wgs84(region)


def _request_geojson(url: str, params: dict, source_label: str) -> dict:
    """GET `url` with `params`, parse the response as GeoJSON, and return
    the decoded dict. Raises ProtectedAreasFetchError on any request or
    parse failure -- never returns a partial/invalid result silently."""
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProtectedAreasFetchError(
            f"{source_label} request failed: {exc}. Endpoint/params were set "
            f"from published documentation, not a verified live response -- "
            f"see the module SOURCE NOTE before assuming the endpoint itself "
            f"is wrong."
        ) from exc
    return data


def _write_geojson(region: Region, geojson: dict) -> Path:
    out_dir = RAW_DIR / region.value
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "protected_areas.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    return out_path


def _query_overpass_protected_areas(bbox_wgs84: tuple[float, float, float, float]) -> dict:
    """POST the Overpass QL protected-areas query for `bbox_wgs84` (minx,
    miny, maxx, maxy), retrying each mirror endpoint on timeout/connection
    error before falling back to the next one -- same resilience pattern
    already used by water_bodies_fetch.py / grid_infrastructure_fetch.py.
    """
    minx, miny, maxx, maxy = bbox_wgs84
    overpass_bbox = f"{miny},{minx},{maxy},{maxx}"
    query = _OVERPASS_PROTECTED_AREAS_QUERY.format(
        timeout=OVERPASS_QUERY_TIMEOUT_S, bbox=overpass_bbox
    )

    last_exc: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(1, OVERPASS_MAX_ATTEMPTS_PER_SERVER + 1):
            try:
                print(f"  querying {url} (attempt {attempt}/{OVERPASS_MAX_ATTEMPTS_PER_SERVER})...",
                      end="", flush=True)
                t0 = time.time()
                resp = requests.post(url, data={"data": query}, headers=OVERPASS_HEADERS,
                                      timeout=OVERPASS_HTTP_TIMEOUT_S)
                resp.raise_for_status()
                data = resp.json()
                print(f" ok in {time.time() - t0:.1f}s")
                return data
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                    ValueError) as exc:
                last_exc = exc
                print(f" failed ({exc})")
    raise ProtectedAreasFetchError(
        f"Overpass protected-areas query failed on all endpoints {OVERPASS_URLS}: {last_exc}"
    )


def _overpass_elements_to_geojson(elements: list[dict]) -> dict:
    """Convert Overpass 'out geom' way/relation elements tagged as
    protected areas into a GeoJSON FeatureCollection. Only closed ways
    (first == last point, a plausible polygon ring) are kept -- a
    protected-area boundary must enclose an area, unlike
    water_bodies_fetch.py's rivers, which are legitimately open
    LineStrings. Relation members are flattened into their individual way
    polygons (inner/outer roles not distinguished), the same accepted
    simplification water_bodies_fetch.py already documents for its own
    relations -- sufficient for a binary exclusion mask, which only needs
    to know where a protected area broadly is, not its exact multipolygon
    topology."""
    features = []
    for el in elements:
        el_type = el.get("type")
        tags = el.get("tags", {})
        ways = []
        if el_type == "way" and "geometry" in el:
            ways = [el["geometry"]]
        elif el_type == "relation":
            ways = [
                member["geometry"] for member in el.get("members", [])
                if member.get("type") == "way" and "geometry" in member
            ]
        for geom_pts in ways:
            coords = [(pt["lon"], pt["lat"]) for pt in geom_pts]
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            try:
                polygon = Polygon(coords)
            except Exception:
                continue
            if not polygon.is_valid or polygon.is_empty:
                continue
            features.append({
                "type": "Feature",
                "geometry": mapping(polygon),
                "properties": tags,
            })
    return {"type": "FeatureCollection", "features": features}


def _fetch_brazil_overpass_fallback(region: Region) -> dict:
    """OSM Overpass fallback for Brazil protected areas (see FALLBACK NOTE
    above): boundary=protected_area / boundary=national_park /
    leisure=nature_reserve, queried directly against OSM rather than
    INDE's WFS, used only when the INDE query fails or returns zero
    features."""
    bbox_wgs84 = _region_bbox_wgs84(region)
    osm_data = _query_overpass_protected_areas(bbox_wgs84)
    elements = osm_data.get("elements", [])
    geojson = _overpass_elements_to_geojson(elements)
    print(
        f"  OSM Overpass fallback: parsed {len(geojson['features'])} protected-area "
        f"polygons from {len(elements)} OSM elements"
    )
    return geojson


def fetch_brazil(config) -> Path:
    """Query INDE's public GeoServer WFS for MMA/ICMBio conservation units
    intersecting Northeast Brazil's bounding box, and write
    data/raw/protected_areas/nordeste_br/protected_areas.geojson. Returns
    the Path.

    `config` is accepted for interface parity with other acquisition
    modules but is not currently used -- the query bbox is defined by the
    region's own boundary geometry, not by the analysis grid.

    Falls back to querying OSM's Overpass API directly (see FALLBACK NOTE
    above) if the INDE WFS request fails or returns zero features.
    """
    t0 = time.time()
    region = Region.NORDESTE_BR
    print(f"[protected_areas] {region.value}: ICMBio/MMA conservation units via INDE WFS")

    minx, miny, maxx, maxy = _region_bbox_wgs84(region)
    # WFS 2.0.0 GetFeature bbox filter with an explicit URN CRS: GeoServer
    # honors the URN's OWN declared axis order (EPSG:4326's URN form is
    # lat,lon), not the lon,lat order a bare "EPSG:4326" suffix implies --
    # see FALLBACK NOTE above for why the previous lon,lat bbox produced an
    # HTTP 400.
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": INDE_LAYER_TYPENAME,
        "outputFormat": "application/json",
        "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326",
    }
    try:
        geojson = _request_geojson(INDE_WFS_BASE, params, "INDE WFS (MMA/ICMBio)")
        features = geojson.get("features", [])
        if not features:
            raise ProtectedAreasFetchError("INDE WFS returned zero features for the region bbox")
    except ProtectedAreasFetchError as exc:
        print(f"  INDE WFS failed ({exc}); falling back to OSM Overpass protected areas")
        geojson = _fetch_brazil_overpass_fallback(region)
        features = geojson.get("features", [])

    print(f"  parsed {len(features)} conservation-unit features")
    if not features:
        raise ProtectedAreasFetchError(
            f"No protected-area features returned for region {region.value!r} from "
            f"either INDE WFS or the OSM Overpass fallback -- an empty result would "
            f"silently produce a meaningless exclusion mask downstream."
        )

    out_path = _write_geojson(region, geojson)
    print(
        f"[protected_areas] {region.value}: wrote {out_path} "
        f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
    )
    return out_path


def fetch_germany(config) -> Path:
    """Query the EEA discomap ArcGIS REST service for Natura 2000 sites
    intersecting North Germany's bounding box, and write
    data/raw/protected_areas/germany/protected_areas.geojson. Returns the
    Path.
    """
    t0 = time.time()
    region = Region.NORTH_GERMANY
    print(f"[protected_areas] {region.value}: Natura 2000 via EEA discomap ArcGIS REST")

    minx, miny, maxx, maxy = _region_bbox_wgs84(region)
    # ArcGIS REST accepts an envelope as a plain "xmin,ymin,xmax,ymax"
    # string, but the JSON-object form with an explicit spatialReference is
    # the documented, unambiguous encoding for esriGeometryEnvelope -- it
    # removes any axis-order/precision ambiguity across FeatureServer vs
    # MapServer query implementations.
    envelope = {
        "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
        "spatialReference": {"wkid": 4326},
    }
    params = {
        "geometry": json.dumps(envelope),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
    }
    geojson = _request_geojson(
        NATURA2000_QUERY_URL, params, "EEA discomap ArcGIS REST (Natura 2000)"
    )
    features = geojson.get("features", [])
    print(f"  parsed {len(features)} Natura 2000 features")
    if not features:
        raise ProtectedAreasFetchError(
            f"No protected-area features returned for region {region.value!r} -- "
            f"an empty result would silently produce a meaningless exclusion "
            f"mask downstream. Check NATURA2000_QUERY_URL and the bbox before "
            f"proceeding."
        )

    out_path = _write_geojson(region, geojson)
    print(
        f"[protected_areas] {region.value}: wrote {out_path} "
        f"({out_path.stat().st_size / 1e6:.2f} MB) in {time.time() - t0:.1f}s"
    )
    return out_path


def fetch(region: Region, config) -> Path:
    """Unified entry point matching the acquisition-layer contract
    (`fetch(region, config) -> Path`, Hard Rule 5, root CLAUDE.md).
    Dispatches to fetch_brazil() or fetch_germany() based on region.
    """
    out_path = RAW_DIR / region.value / "protected_areas.geojson"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[protected_areas] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    if region == Region.NORDESTE_BR:
        return fetch_brazil(config)
    if region == Region.NORTH_GERMANY:
        return fetch_germany(config)
    raise ValueError(f"Unrecognized region: {region!r}")
