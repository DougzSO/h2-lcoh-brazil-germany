"""Diagnostic script (change_list.md item 5) -- standalone, read-only.

Loads the raw data/raw/landuse/brazil/landuse.tif (MapBiomas Collection 9,
native EPSG:4326, ~204 m pixel size -- NOT the 1 km analysis grid) and
computes a class-code histogram converted to km2, to check whether the
3.3% exclusion currently reported by src/spatial/exclusion_mask.py
(classes {11, 24, 30, 33} + nodata) is defensible against MapBiomas
Collection 9's published Northeast Brazil statistics, or whether a
high-coverage class that should be excluded is being missed.

Does NOT import or modify exclusion_mask.py. Read-only against the raw
raster already on disk -- no re-download, no pipeline stage re-run.
"""

from __future__ import annotations

import numpy as np
import rasterio

LANDUSE_PATH = "data/raw/landuse/brazil/landuse.tif"

# MapBiomas Collection 9 legend (relevant codes only, for the printed
# table's "Description" column) -- https://mapbiomas.org (published legend).
MAPBIOMAS_LEGEND = {
    0: "nodata / unclassified",
    3: "Forest Formation",
    4: "Savanna Formation",
    5: "Mangrove",
    9: "Forest Plantation",
    11: "Wetland",
    12: "Grassland",
    15: "Pasture",
    20: "Sugar Cane",
    21: "Mosaic of Uses",
    23: "Beach, Dune and Sand Spot",
    24: "Urban Area",
    25: "Other non-vegetated areas",
    29: "Rocky Outcrop",
    30: "Mining",
    33: "River, Lake and Ocean",
    41: "Other Temporary Crops",
}

EXCLUDED_CLASSES = {11, 24, 30, 33}  # matches exclusion_mask.MAPBIOMAS_EXCLUDED_CLASSES
NODATA_CLASS_CODE = 0

EARTH_DEG_TO_KM = 111.32  # km per degree of latitude (approx, WGS84 mean)


def main() -> None:
    with rasterio.open(LANDUSE_PATH) as src:
        array = src.read(1)
        transform = src.transform
        crs = src.crs
        height, width = array.shape

    pixel_width_deg = abs(transform.a)
    pixel_height_deg = abs(transform.e)

    # Geographic CRS (EPSG:4326): pixel area in km2 varies with latitude
    # (longitude distance shrinks by cos(lat)). Compute per-row pixel area
    # rather than a single flat conversion factor, and accumulate area per
    # class code row-by-row -- exact rather than a central-latitude
    # approximation (the raster spans -18.34 deg to -1.05 deg, a ~5% cos
    # spread top-to-bottom, non-negligible for a headline number).
    row_lats = transform.f + (np.arange(height) + 0.5) * transform.e
    row_width_km = pixel_width_deg * EARTH_DEG_TO_KM * np.cos(np.radians(row_lats))
    row_height_km = pixel_height_deg * EARTH_DEG_TO_KM
    row_pixel_area_km2 = row_width_km * row_height_km  # (height,)

    unique_classes = np.unique(array)
    class_area_km2 = {}
    class_pixel_count = {}
    for code in unique_classes:
        rows_with_code, _ = np.where(array == code)
        # Sum each matching pixel's own row area (vectorized via bincount).
        counts_per_row = np.bincount(rows_with_code, minlength=height)
        class_area_km2[int(code)] = float((counts_per_row * row_pixel_area_km2).sum())
        class_pixel_count[int(code)] = int((array == code).sum())

    total_pixels = array.size
    total_area_km2 = sum(class_area_km2.values())

    print(f"=== Brazil land-use raster diagnostic: {LANDUSE_PATH} ===")
    print(f"CRS: {crs}, shape: {array.shape}, dtype: {array.dtype}")
    print(
        f"Native pixel size: {pixel_width_deg:.6f} x {pixel_height_deg:.6f} deg "
        f"(~{pixel_width_deg*EARTH_DEG_TO_KM*1000:.0f} x "
        f"{pixel_height_deg*EARTH_DEG_TO_KM*1000:.0f} m at the equator)"
    )
    print(f"Total pixels: {total_pixels:,}  Total area (lat-corrected): {total_area_km2:,.1f} km2\n")

    print(f"{'Class Code':>10} | {'Description':<28} | {'Pixel Count':>12} | {'Area (km2)':>14} | {'% of Total':>10} | Excluded?")
    print("-" * 100)
    excluded_area = 0.0
    for code in sorted(class_area_km2, key=lambda c: -class_area_km2[c]):
        area = class_area_km2[code]
        pct = 100.0 * area / total_area_km2
        desc = MAPBIOMAS_LEGEND.get(code, "(code not in legend excerpt above)")
        is_excluded = code in EXCLUDED_CLASSES or code == NODATA_CLASS_CODE
        if is_excluded:
            excluded_area += area
        marker = "EXCLUDED" if is_excluded else ""
        print(f"{code:>10} | {desc:<28} | {class_pixel_count[code]:>12,} | {area:>14,.1f} | {pct:>9.2f}% | {marker}")

    pct_excluded = 100.0 * excluded_area / total_area_km2
    print("-" * 100)
    print(
        f"Sum of excluded classes {sorted(EXCLUDED_CLASSES)} + nodata({NODATA_CLASS_CODE}): "
        f"{excluded_area:,.1f} km2 = {pct_excluded:.2f}% of raw raster area"
    )
    print(
        "\nNote: this is the RAW raster's own exclusion share (native ~204 m pixels, "
        "full raster bbox incl. any area outside the true dissolved 9-state boundary). "
        "exclusion_mask.py's reported 3.3% is computed differently -- on the 1 km analysis "
        "grid, AND-ed against the true dissolved-boundary mask (not the raw raster's "
        "rectangular extent) and against the protected-areas mask. The two numbers are "
        "not expected to match exactly; this script checks whether the land-use layer's "
        "OWN class composition is plausible and complete, independent of the boundary/"
        "protected-area AND-ing exclusion_mask.py additionally applies."
    )

    print("\n=== MapBiomas Collection 9 Northeast Brazil comparison (published, approximate) ===")
    checks = [
        (24, "Urban", 0.5, class_area_km2.get(24, 0.0) / total_area_km2 * 100),
        (33, "Water bodies", 1.0, class_area_km2.get(33, 0.0) / total_area_km2 * 100),
        (11, "Wetland", 0.0, class_area_km2.get(11, 0.0) / total_area_km2 * 100),
        (30, "Mining", 0.0, class_area_km2.get(30, 0.0) / total_area_km2 * 100),
    ]
    for code, label, expected_min, observed in checks:
        status = "OK" if observed >= expected_min else "BELOW EXPECTED MINIMUM"
        print(f"  Class {code} ({label}): observed {observed:.3f}% vs expected >{expected_min}% -> {status}")


if __name__ == "__main__":
    main()
