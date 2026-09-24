#!/usr/bin/env python3
"""Prepare a compact population reference for Climate Pulse.

Source: European Commission JRC GHSL GHS-WUP-POP R2025A, epoch 2025.

The authoritative 1 km source raster is downloaded transiently during CI and is
NOT committed to GitHub. A compact 0.1 degree WGS84 population-count raster is
created for fast screening / visualization and committed with provenance.

Important: final event-exposure calculations should use the authoritative 1 km
source (downloaded/cached in the backend workflow) whenever a hazard footprint
is available. The 0.1 degree derivative is a convenience product, not the
highest-accuracy exposure layer.
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

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "reference" / "population"
OUT_TIF = OUT_DIR / "ghsl_wup_2025_0p1deg.tif"
OUT_META = OUT_DIR / "metadata.json"
OUT_README = OUT_DIR / "README.md"

DATASET_PAGE = "https://human-settlement.emergency.copernicus.eu/ghs_wup_pop_r2025a.php"
DATASET_DOI = "10.2905/adba95af-db56-4569-acd3-9513201eba30"
DATASET_NAME = "GHS_WUP_POP_GLOBE_R2025A"
EPOCH = 2025
TARGET_RESOLUTION_DEG = 0.1

# GHSL/JRC FTP follows a stable product/dataset/version directory convention.
# Prefer the 1 km equal-area source; keep the WGS84 30 arcsec product as a
# fallback so the workflow can recover if the preferred file path changes.
SOURCE_CANDIDATES = [
    (
        "GHS_WUP_POP_E2025_GLOBE_R2025A_54009_1000_V1_0",
        "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
        "GHS_WUP_POP_GLOBE_R2025A/"
        "GHS_WUP_POP_E2025_GLOBE_R2025A_54009_1000/V1-0/"
        "GHS_WUP_POP_E2025_GLOBE_R2025A_54009_1000_V1_0.zip",
    ),
    (
        "GHS_WUP_POP_E2025_GLOBE_R2025A_4326_30ss_V1_0",
        "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
        "GHS_WUP_POP_GLOBE_R2025A/"
        "GHS_WUP_POP_E2025_GLOBE_R2025A_4326_30ss/V1-0/"
        "GHS_WUP_POP_E2025_GLOBE_R2025A_4326_30ss_V1_0.zip",
    ),
]

USER_AGENT = "ClimatePulse/0.2 (+https://zhongzq23-design.github.io/climate-pulse/)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def find_source_tif(extract_dir: Path) -> Path:
    tifs = [p for p in extract_dir.rglob("*.tif") if p.is_file()]
    if not tifs:
        raise RuntimeError("GHSL archive contained no GeoTIFF")
    # Prefer the main population raster over ancillary files if present.
    tifs.sort(key=lambda p: ("POP" not in p.name.upper(), len(p.name)))
    return tifs[0]


def source_sum(src: rasterio.io.DatasetReader) -> float:
    total = 0.0
    nodata = src.nodata
    for _, window in src.block_windows(1):
        arr = src.read(1, window=window, masked=False).astype("float64", copy=False)
        mask = np.isfinite(arr)
        if nodata is not None:
            mask &= arr != nodata
        mask &= arr >= 0
        total += float(arr[mask].sum(dtype="float64"))
    return total


def build_compact_raster(src_path: Path) -> dict[str, float | int | str | None]:
    width = round(360 / TARGET_RESOLUTION_DEG)
    height = round(180 / TARGET_RESOLUTION_DEG)
    dst_transform = from_origin(-180.0, 90.0, TARGET_RESOLUTION_DEG, TARGET_RESOLUTION_DEG)
    dst = np.zeros((height, width), dtype="float32")

    with rasterio.open(src_path) as src:
        if not src.crs:
            raise RuntimeError("GHSL source raster has no CRS")
        src_total = source_sum(src)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata if src.nodata is not None else -200,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            dst_nodata=0.0,
            resampling=Resampling.sum,
            num_threads=2,
            init_dest_nodata=True,
        )
        src_crs = src.crs.to_string()
        src_width, src_height = src.width, src.height
        src_nodata = src.nodata

    bad = ~np.isfinite(dst) | (dst < 0)
    dst[bad] = 0.0
    compact_total = float(dst.sum(dtype="float64"))

    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": dst_transform,
        "nodata": 0.0,
        "compress": "DEFLATE",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "BIGTIFF": "IF_SAFER",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with rasterio.open(OUT_TIF, "w", **profile) as dst_ds:
        dst_ds.write(dst, 1)
        dst_ds.update_tags(
            SOURCE_DATASET=DATASET_NAME,
            SOURCE_EPOCH=str(EPOCH),
            SOURCE_DOI=DATASET_DOI,
            PROCESSING="Population-count sum resampling to 0.1 degree WGS84",
        )

    return {
        "source_crs": src_crs,
        "source_width": src_width,
        "source_height": src_height,
        "source_nodata": src_nodata,
        "source_population_sum": src_total,
        "derived_population_sum": compact_total,
        "relative_total_difference": (compact_total - src_total) / src_total if src_total else None,
        "derived_width": width,
        "derived_height": height,
    }


def write_readme() -> None:
    OUT_README.write_text(
        """# Population reference\n\n"
        "Climate Pulse uses the European Commission JRC **GHSL GHS-WUP-POP R2025A** "
        "population product as its default population reference.\n\n"
        "## Files committed here\n\n"
        "- `ghsl_wup_2025_0p1deg.tif` — derived 0.1° WGS84 population-count grid for "
        "fast screening, visualization, and approximate proximity checks.\n"
        "- `metadata.json` — provenance, processing details, source/derived totals, and hashes.\n\n"
        "## Accuracy rule\n\n"
        "The 0.1° derivative is **not** the preferred layer for final event-exposure statistics. "
        "When a wildfire, flood, cyclone, heat, drought, or landslide footprint is available, "
        "the backend should use the authoritative GHSL 1 km source raster and commit only the "
        "per-event exposure result. This keeps GitHub lightweight while preserving analysis quality.\n\n"
        "## Source\n\n"
        "European Commission Joint Research Centre, GHS-WUP-POP R2025A, epoch 2025. "
        f"DOI: {DATASET_DOI}. Reuse is permitted with proper acknowledgement of the source.\n"
        """,
        encoding="utf-8",
    )


def main() -> None:
    force = os.getenv("FORCE_POPULATION_REBUILD") == "1"
    if OUT_TIF.exists() and OUT_META.exists() and not force:
        print(f"Population reference already exists: {OUT_TIF}")
        return

    with tempfile.TemporaryDirectory(prefix="climate-pulse-pop-") as td:
        temp = Path(td)
        archive = temp / "ghsl.zip"
        used_name = used_url = None
        errors: list[str] = []
        for name, url in SOURCE_CANDIDATES:
            try:
                print(f"Downloading {name} ...")
                download(url, archive)
                used_name, used_url = name, url
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                if archive.exists():
                    archive.unlink()
        if not used_url:
            raise RuntimeError("All GHSL download candidates failed: " + " | ".join(errors))

        extract_dir = temp / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
        src_tif = find_source_tif(extract_dir)
        print(f"Using source raster: {src_tif.name}")

        stats = build_compact_raster(src_tif)
        write_readme()
        size_bytes = OUT_TIF.stat().st_size
        if size_bytes >= 95 * 1024 * 1024:
            raise RuntimeError(
                f"Derived GeoTIFF is {size_bytes / 1024 / 1024:.1f} MiB; refusing to commit a near-GitHub-limit file"
            )

        meta = {
            "schema_version": "1.0",
            "generated_at": now_iso(),
            "dataset": DATASET_NAME,
            "epoch": EPOCH,
            "dataset_page": DATASET_PAGE,
            "doi": DATASET_DOI,
            "reuse": "European Union data; reuse authorised with proper acknowledgement of the source",
            "authoritative_source_file": used_name,
            "authoritative_source_url": used_url,
            "authoritative_source_committed_to_git": False,
            "derived_product": {
                "path": str(OUT_TIF.relative_to(ROOT)).replace("\\", "/"),
                "crs": "EPSG:4326",
                "resolution_degrees": TARGET_RESOLUTION_DEG,
                "value": "population count per cell",
                "purpose": "fast screening / visualization; use authoritative 1 km GHSL for final exposure calculations",
                "file_size_bytes": size_bytes,
                "sha256": sha256(OUT_TIF),
            },
            "validation": stats,
        }
        OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
