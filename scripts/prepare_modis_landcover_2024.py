#!/usr/bin/env python3
"""Build compact 0.05° land/forest-area rasters from NASA MCD12C1.061 2024.

NASA distributes MCD12C1 through Earthdata-controlled download. Set an
``EARTHDATA_TOKEN`` secret in GitHub Actions. The raw HDF4 granule is transient /
cache-only; only the derived two-band uint16 GeoTIFF is committed.

Band 1 = land area (ha), using 100 - IGBP water-class percentage.
Band 2 = forest area (ha), sum of IGBP classes 1-5 percentages.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from pyhdf.SD import SD, SDC
from pyproj import Geod
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "reference" / "landcover" / "modis_mcd12c1_2024"
META = OUT_DIR / "metadata.json"
OUT_TIF = OUT_DIR / "land_forest_area_ha_0p05.tif"
CACHE_ROOT = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse"))
HDF_PATH = CACHE_ROOT / "modis" / "MCD12C1.A2024001.061.2025216131527.hdf"
URL = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/MCD12C1.061/MCD12C1.A2024001.061.2025216131527/MCD12C1.A2024001.061.2025216131527.hdf"
NY, NX, NCLASS = 3600, 7200, 17
GEOD = Geod(ellps="WGS84")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ensure_hdf() -> Path | None:
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if not token:
        print("EARTHDATA_TOKEN is not configured; MODIS reference build skipped.")
        return None
    HDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if HDF_PATH.exists() and HDF_PATH.stat().st_size > 1_000_000:
        print(f"Using cached MCD12C1 HDF: {HDF_PATH}")
        return HDF_PATH
    HDF_PATH.unlink(missing_ok=True)
    req = urllib.request.Request(
        URL,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "ClimatePulse/0.7"},
    )
    print("Downloading NASA MCD12C1.061 2024 via Earthdata authentication")
    with urllib.request.urlopen(req, timeout=300) as resp, HDF_PATH.open("wb") as out:
        shutil.copyfileobj(resp, out, length=8 * 1024 * 1024)
    if HDF_PATH.stat().st_size < 1_000_000:
        raise RuntimeError("Downloaded MCD12C1 file is unexpectedly small")
    return HDF_PATH


def orient_percent(raw: np.ndarray) -> np.ndarray:
    arr = np.asarray(raw)
    if arr.ndim != 3:
        raise RuntimeError(f"Expected 3-D Land_Cover_Type_1_Percent, got {arr.shape}")
    class_axes = [i for i, n in enumerate(arr.shape) if n == NCLASS]
    if len(class_axes) != 1:
        raise RuntimeError(f"Could not identify 17-class axis in {arr.shape}")
    arr = np.moveaxis(arr, class_axes[0], 0)
    if arr.shape[1:] == (NX, NY):
        arr = arr.transpose(0, 2, 1)
    if arr.shape != (NCLASS, NY, NX):
        raise RuntimeError(f"Unexpected oriented percent shape {arr.shape}")
    # MCD12C1 CMG is distributed north-to-south / west-to-east on the global
    # latitude-longitude grid. Values >100 are fill/invalid.
    arr = arr.astype("float32", copy=False)
    arr[(arr < 0) | (arr > 100)] = 0
    return arr


def row_cell_area_ha() -> np.ndarray:
    result = np.empty(NY, dtype="float64")
    for r in range(NY):
        top = 90.0 - r * 0.05
        bottom = top - 0.05
        lons = [-180.0, -179.95, -179.95, -180.0, -180.0]
        lats = [top, top, bottom, bottom, top]
        area_m2, _ = GEOD.polygon_area_perimeter(lons, lats)
        result[r] = abs(area_m2) / 10_000.0
    return result


def build(hdf_path: Path) -> tuple[float, float]:
    sd = SD(str(hdf_path), SDC.READ)
    try:
        if "Land_Cover_Type_1_Percent" not in sd.datasets():
            raise RuntimeError(
                "MCD12C1 HDF lacks Land_Cover_Type_1_Percent; available SDS: "
                + ", ".join(list(sd.datasets())[:20])
            )
        percent = orient_percent(sd.select("Land_Cover_Type_1_Percent")[:])
    finally:
        sd.end()

    water_pct = percent[0]
    forest_pct = np.sum(percent[1:6], axis=0, dtype="float32")
    forest_pct = np.clip(forest_pct, 0, 100)
    land_pct = np.clip(100.0 - water_pct, 0, 100)
    area_ha = row_cell_area_ha()[:, None]
    land_ha = np.rint(area_ha * land_pct / 100.0)
    forest_ha = np.rint(area_ha * forest_pct / 100.0)
    land = np.clip(land_ha, 0, 65534).astype("uint16")
    forest = np.clip(forest_ha, 0, 65534).astype("uint16")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": NY,
        "width": NX,
        "count": 2,
        "dtype": "uint16",
        "crs": "EPSG:4326",
        "transform": from_origin(-180.0, 90.0, 0.05, 0.05),
        "nodata": 65535,
        "compress": "DEFLATE",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(OUT_TIF, "w", **profile) as dst:
        dst.write(land, 1)
        dst.write(forest, 2)
        dst.set_band_description(1, "MODIS MCD12C1 2024 land area (ha per 0.05 degree cell)")
        dst.set_band_description(2, "MODIS MCD12C1 2024 IGBP forest classes 1-5 area (ha per 0.05 degree cell)")
        dst.update_tags(
            source="NASA MODIS MCD12C1.061",
            source_doi="10.5067/MODIS/MCD12C1.061",
            reference_year="2024",
            land_definition="100 - IGBP class 0 water percent",
            forest_definition="IGBP percent classes 1-5",
            unit="ha",
        )
    return float(np.max(land)), float(np.max(forest))


def update_metadata(hdf_path: Path, max_land: float, max_forest: float) -> None:
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    meta.update({
        "status": "ready",
        "generated_at": now_iso(),
        "source_hdf_bytes": hdf_path.stat().st_size,
        "source_hdf_sha256": sha256_file(hdf_path),
        "derived_grid": "EPSG:4326, 0.05 degree, 3600x7200, two uint16 hectare bands",
        "derived_bytes": OUT_TIF.stat().st_size,
        "derived_sha256": sha256_file(OUT_TIF),
        "max_cell_land_area_ha": round(max_land, 1),
        "max_cell_forest_area_ha": round(max_forest, 1),
        "boundary_use": "event overlay uses grid-cell-centre inclusion; source polygon remains unsimplified",
    })
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    hdf = ensure_hdf()
    if hdf is None:
        return
    max_land, max_forest = build(hdf)
    update_metadata(hdf, max_land, max_forest)
    print(json.dumps({
        "status": "ok",
        "output": str(OUT_TIF.relative_to(ROOT)),
        "bytes": OUT_TIF.stat().st_size,
        "sha256": sha256_file(OUT_TIF),
    }, indent=2))


if __name__ == "__main__":
    main()
