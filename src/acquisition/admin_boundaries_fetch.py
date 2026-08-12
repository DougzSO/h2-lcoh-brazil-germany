"""Downloads raw admin boundary features (IBGE for NE Brazil, GADM for
North Germany) and writes each to data/boundaries/. Dissolving/reprojection
stay in spatial/admin_boundaries.py, which reads the files this module writes."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

from src.core.constants import Region

RAW_DIR = Path("data/boundaries")
NORDESTE_STATE_CODES = {"MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25,
                         "PE": 26, "AL": 27, "SE": 28, "BA": 29}
NORTH_GERMANY_STATES = {"Schleswig-Holstein", "Niedersachsen"}
IBGE_MALHAS_BASE = "https://servicodados.ibge.gov.br/api/v3/malhas"
GADM_ZIP_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_DEU_shp.zip"
TIMEOUT_S = 45


class BoundaryFetchError(RuntimeError):
    """Raised when a boundary source cannot be downloaded or parsed."""


def _fetch_brazil() -> gpd.GeoDataFrame:
    frames = []
    for uf, code in NORDESTE_STATE_CODES.items():
        url = f"{IBGE_MALHAS_BASE}/estados/{code}?formato=application/vnd.geo+json"
        try:
            resp = requests.get(url, timeout=TIMEOUT_S)
            resp.raise_for_status()
            frames.append(gpd.GeoDataFrame.from_features(json.loads(resp.content)["features"]))
        except Exception as exc:
            raise BoundaryFetchError(f"Failed to fetch IBGE mesh for {uf}: {exc}") from exc
    return gpd.GeoDataFrame(gpd.pd.concat(frames, ignore_index=True), crs="EPSG:4326")


def _fetch_germany() -> gpd.GeoDataFrame:
    try:
        resp = requests.get(GADM_ZIP_URL, timeout=TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise BoundaryFetchError(f"Failed to download GADM DEU zip: {exc}") from exc
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "gadm41_DEU_shp.zip"
        zip_path.write_bytes(resp.content)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        shps = sorted(Path(tmp).glob("*_1.shp"))
        if not shps:
            raise BoundaryFetchError("No level-1 shapefile found in GADM DEU zip.")
        gdf_all = gpd.read_file(str(shps[0]))
    matched = gdf_all[gdf_all["NAME_1"].isin(NORTH_GERMANY_STATES)].copy()
    if len(matched) < len(NORTH_GERMANY_STATES):
        raise BoundaryFetchError(
            f"Matched only {len(matched)}/{len(NORTH_GERMANY_STATES)} required German states.")
    return matched.set_crs("EPSG:4326") if matched.crs is None else matched.to_crs("EPSG:4326")


def fetch(region: Region, config) -> Path:
    """Download raw boundary features for `region`, write to
    data/boundaries/<region>.geojson, return the Path."""
    out_path = RAW_DIR / f"{region.value}.geojson"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[admin_boundaries] {region.value}: using cached {out_path} (skipped download)")
        return out_path

    if region == Region.NORDESTE_BR:
        gdf = _fetch_brazil()
    elif region == Region.NORTH_GERMANY:
        gdf = _fetch_germany()
    else:
        raise ValueError(f"Unrecognized region: {region!r}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    gdf[["geometry"]].to_file(out_path, driver="GeoJSON")
    return out_path
