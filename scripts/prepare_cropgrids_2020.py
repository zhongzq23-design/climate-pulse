#!/usr/bin/env python3
"""Build a compact global 0.05° total crop-physical-area raster from CROPGRIDS.

The authoritative CROPGRIDS v1.08 distribution contains one NetCDF per crop.
This builder downloads the public Figshare archive once, streams the 173 crop
files one at a time, sums their ``croparea`` variable, and commits only a compact
uint16 GeoTIFF (hectares per 0.05° cell). The ~807 MB source archive is cached but
never committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import netCDF4
import numpy as np
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "reference" / "crops" / "cropgrids_2020"
META = OUT_DIR / "metadata.json"
OUT_TIF = OUT_DIR / "crop_area_ha_0p05.tif"
CACHE_ROOT = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse"))
ZIP_PATH = CACHE_ROOT / "cropgrids" / "CROPGRIDSv1.08_NC_maps.zip"
URL = "https://ndownloader.figshare.com/files/44950942"
EXPECTED_MD5 = "2773d0b1f83518cf7a4e271a73e62a57"
EXPECTED_BYTES = 806_855_203
NY, NX = 3600, 7200


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def md5_file(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - source-integrity checksum published by Figshare
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ensure_zip() -> Path:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists() and ZIP_PATH.stat().st_size == EXPECTED_BYTES and md5_file(ZIP_PATH) == EXPECTED_MD5:
        print(f"Using cached CROPGRIDS archive: {ZIP_PATH}")
        return ZIP_PATH
    ZIP_PATH.unlink(missing_ok=True)
    print(f"Downloading CROPGRIDS v1.08 ({EXPECTED_BYTES / 1e6:.1f} MB) from Figshare")
    req = urllib.request.Request(URL, headers={"User-Agent": "ClimatePulse/0.7"})
    with urllib.request.urlopen(req, timeout=300) as resp, ZIP_PATH.open("wb") as out:
        shutil.copyfileobj(resp, out, length=8 * 1024 * 1024)
    if ZIP_PATH.stat().st_size != EXPECTED_BYTES:
        raise RuntimeError(f"Unexpected CROPGRIDS archive size: {ZIP_PATH.stat().st_size}")
    got = md5_file(ZIP_PATH)
    if got != EXPECTED_MD5:
        raise RuntimeError(f"CROPGRIDS MD5 mismatch: {got}")
    return ZIP_PATH


def coordinate_var(ds, candidates):
    lower = {str(k).lower(): k for k in ds.variables}
    for name in candidates:
        if name in lower:
            return np.asarray(ds.variables[lower[name]][:]).squeeze()
    return None


def orient_crop_area(ds) -> np.ndarray:
    if "croparea" not in ds.variables:
        # Defensive case-insensitive fallback.
        key = next((k for k in ds.variables if str(k).lower() == "croparea"), None)
        if key is None:
            raise RuntimeError("NetCDF has no croparea variable")
    else:
        key = "croparea"
    raw = ds.variables[key][:]
    arr = np.ma.filled(raw, 0).astype("float32", copy=False).squeeze()
    lat = coordinate_var(ds, ("lat", "latitude", "y"))
    lon = coordinate_var(ds, ("lon", "longitude", "x"))
    if lat is None or lon is None:
        raise RuntimeError("CROPGRIDS NetCDF lacks latitude/longitude coordinate variables")
    if arr.shape == (len(lon), len(lat)):
        arr = arr.T
    if arr.shape != (len(lat), len(lon)):
        raise RuntimeError(f"Unexpected croparea shape {arr.shape}; lat={len(lat)} lon={len(lon)}")
    if len(lat) != NY or len(lon) != NX:
        raise RuntimeError(f"Expected 3600x7200 grid, got {len(lat)}x{len(lon)}")
    # GeoTIFF output is north-to-south, west-to-east.
    if float(lat[0]) < float(lat[-1]):
        arr = arr[::-1, :]
        lat = lat[::-1]
    lon = np.asarray(lon, dtype="float64")
    if float(lon.min()) >= -1e-6 and float(lon.max()) > 180:
        arr = np.roll(arr, NX // 2, axis=1)
    elif not (float(lon.min()) < 0 < float(lon.max())):
        raise RuntimeError(f"Unexpected longitude range {lon.min()}..{lon.max()}")
    arr[~np.isfinite(arr)] = 0
    arr[arr < 0] = 0
    return arr


def build() -> tuple[int, float]:
    zpath = ensure_zip()
    total = np.zeros((NY, NX), dtype="float32")
    count = 0
    with zipfile.ZipFile(zpath) as zf, tempfile.TemporaryDirectory(prefix="cp-cropgrids-") as td:
        members = [m for m in zf.namelist() if m.lower().endswith(".nc") and "countries_" not in m.lower()]
        if len(members) < 170:
            raise RuntimeError(f"Expected ~173 crop NetCDFs, found {len(members)}")
        tmp = Path(td) / "crop.nc"
        for i, member in enumerate(sorted(members), start=1):
            with zf.open(member) as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            with netCDF4.Dataset(tmp, "r") as ds:
                total += orient_crop_area(ds)
            count += 1
            if i == 1 or i % 20 == 0 or i == len(members):
                print(f"CROPGRIDS: summed {i}/{len(members)} crop maps")

    max_ha = float(np.nanmax(total))
    if max_ha >= 65_000:
        raise RuntimeError(f"Crop-area sum exceeds uint16 design range: {max_ha} ha/cell")
    out = np.rint(np.clip(total, 0, 65534)).astype("uint16")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": NY,
        "width": NX,
        "count": 1,
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
        dst.write(out, 1)
        dst.set_band_description(1, "CROPGRIDS 2020 total crop physical area (ha per 0.05 degree cell)")
        dst.update_tags(
            source="CROPGRIDS v1.08",
            source_doi="10.6084/m9.figshare.22491997.v9",
            paper_doi="10.1038/s41597-024-03247-7",
            reference_year="2020",
            unit="ha",
        )
    return count, max_ha


def update_metadata(count: int, max_ha: float) -> None:
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    meta.update({
        "status": "ready",
        "generated_at": now_iso(),
        "crop_maps_summed": count,
        "derived_grid": "EPSG:4326, 0.05 degree, 3600x7200, uint16 hectares",
        "derived_bytes": OUT_TIF.stat().st_size,
        "derived_sha256": sha256_file(OUT_TIF),
        "max_cell_crop_area_ha": round(max_ha, 2),
        "aggregation": "sum of CROPGRIDS croparea (physical area) across all distributed crop NetCDF files; values rounded to nearest hectare",
        "boundary_use": "event overlay uses grid-cell-centre inclusion; source polygon remains unsimplified",
    })
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    count, max_ha = build()
    update_metadata(count, max_ha)
    print(json.dumps({
        "status": "ok",
        "crop_maps": count,
        "output": str(OUT_TIF.relative_to(ROOT)),
        "bytes": OUT_TIF.stat().st_size,
        "sha256": sha256_file(OUT_TIF),
    }, indent=2))


if __name__ == "__main__":
    main()
