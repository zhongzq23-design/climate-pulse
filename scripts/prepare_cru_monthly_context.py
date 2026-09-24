#!/usr/bin/env python3
"""Build compact CRU-TS v4.10 same-month climate context products.

This intentionally does NOT archive the full 1901-2025 monthly CRU record.
Instead it stores two compact 12-month climatologies on the native 0.5° grid:

* 1981-2010: 30-year reference climatology used as the seasonal normal.
* 2016-2025: latest complete 10-year CRU period used as a recent same-month context.

Variables stored:
* tmp: monthly mean temperature [degC]
* pre: mean monthly precipitation total for that calendar month [mm/month]
* vpd: monthly VPD [hPa], derived BEFORE temporal averaging from CRU tmp + vap

VPD follows the Climate Pulse CRU method:
    VPD = SVP - AVP
    SVP = 6.1078 * exp(a*T/(T+b)) [hPa]
with a=17.269,b=237.3 for T>=0 C and a=21.875,b=265.5 for T<0 C.
AVP is CRU monthly vap [hPa].

The product is intended to preserve the seasonal cycle while remaining compact.
It does not represent the actual 2026 event-month weather: CRU-TS v4.10 ends in
2025. Current-event-month anomalies require a near-real-time source such as ERA5.
"""
from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset, num2date

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "data" / "reference" / "climate" / "cru_ts_4.10"
MONTHLY_DIR = OUT_ROOT / "monthly"
META_PATH = MONTHLY_DIR / "metadata.json"

VERSION = "4.10"
RELEASE_FOLDER = "cruts.2604091129.v4.10"
CRU_HOME = "https://crudata.uea.ac.uk/cru/data/hrg/cru_ts_4.10/"
CRU_DATA_BASE = CRU_HOME + RELEASE_FOLDER + "/"
USER_AGENT = "ClimatePulse/0.5 (+https://zhongzq23-design.github.io/climate-pulse/)"
FILL = np.float32(9.96921e36)

PERIODS = {
    "climatology_1981_2010": (1981, 2010),
    "recent_2016_2025": (2016, 2025),
}
BLOCKS = [
    (1981, 1990),
    (1991, 2000),
    (2001, 2010),
    (2011, 2020),
    (2021, 2025),
]
VARIABLES = ("tmp", "pre", "vap")

VPD_REFERENCE = "Zhong Z, Chen HW, Dai A et al. (2025), Nature Communications 16, 8247"
VPD_REFERENCE_DOI = "https://doi.org/10.1038/s41467-025-63672-z"
CRU_REFERENCE = "Harris I, Osborn TJ, Jones P and Lister D (2020), Scientific Data 7, 109"
CRU_REFERENCE_DOI = "https://doi.org/10.1038/s41597-020-0453-3"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def source_url(var: str, y0: int, y1: int) -> str:
    name = f"cru_ts{VERSION}.{y0}.{y1}.{var}.dat.nc.gz"
    return f"{CRU_DATA_BASE}{var}/{name}"


def download(url: str, dest: Path, attempts: int = 4) -> None:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
            if dest.stat().st_size < 1024:
                raise RuntimeError(f"Downloaded file unexpectedly small: {dest.stat().st_size} bytes")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if dest.exists():
                dest.unlink()
            if attempt < attempts:
                time.sleep(4 * attempt)
    raise RuntimeError(f"Failed to download {url}: {last}")


def gunzip(src: Path, dest: Path) -> None:
    with gzip.open(src, "rb") as inp, dest.open("wb") as out:
        shutil.copyfileobj(inp, out, length=1024 * 1024)


def get_coord(ds: Dataset, candidates: tuple[str, ...]) -> np.ndarray:
    for name in candidates:
        if name in ds.variables:
            return np.asarray(ds.variables[name][:], dtype="float64")
    raise RuntimeError(f"Missing coordinate; tried {candidates}")


def get_data_var(ds: Dataset, name: str):
    if name in ds.variables:
        return ds.variables[name]
    for k, v in ds.variables.items():
        if k.lower() == name.lower():
            return v
    raise RuntimeError(f"Variable {name!r} not found")


def dates_from(ds: Dataset) -> list[Any]:
    t = ds.variables.get("time")
    if t is None:
        raise RuntimeError("CRU NetCDF has no time coordinate")
    return list(num2date(
        t[:],
        units=getattr(t, "units"),
        calendar=getattr(t, "calendar", "standard"),
        only_use_cftime_datetimes=True,
    ))


def saturation_vapour_pressure_hpa(tmp_c: np.ma.MaskedArray) -> np.ma.MaskedArray:
    warm = tmp_c >= 0.0
    a = np.ma.where(warm, 17.269, 21.875)
    b = np.ma.where(warm, 237.3, 265.5)
    return 6.1078 * np.ma.exp((a * tmp_c) / (tmp_c + b))


def add_month(sum_arr: np.ndarray, count_arr: np.ndarray, month_idx: int, arr: np.ma.MaskedArray) -> None:
    vals = np.ma.filled(np.ma.asarray(arr, dtype="float64"), np.nan)
    valid = np.isfinite(vals)
    sum_arr[month_idx][valid] += vals[valid]
    count_arr[month_idx][valid] += 1


def mean_from(sum_arr: np.ndarray, count_arr: np.ndarray) -> np.ndarray:
    out = np.full(sum_arr.shape, FILL, dtype="float32")
    ok = count_arr > 0
    out[ok] = (sum_arr[ok] / count_arr[ok]).astype("float32")
    return out


def download_block(y0: int, y1: int, folder: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for var in VARIABLES:
        gz = folder / f"{var}.nc.gz"
        nc = folder / f"{var}.nc"
        url = source_url(var, y0, y1)
        print(f"Downloading {url}")
        download(url, gz)
        print(f"Decompressing {gz.name} ({gz.stat().st_size / 1024 / 1024:.1f} MiB)")
        gunzip(gz, nc)
        gz.unlink()
        paths[var] = nc
    return paths


def build_period(label: str, start_year: int, end_year: int) -> Path:
    out_path = MONTHLY_DIR / f"{label}.nc"
    if out_path.exists() and out_path.stat().st_size > 100_000:
        print(f"{out_path.name} already exists; skipping")
        return out_path

    lat0 = lon0 = None
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, np.ndarray] = {}

    for y0, y1 in BLOCKS:
        if y1 < start_year or y0 > end_year:
            continue
        with tempfile.TemporaryDirectory(prefix=f"cru-monthly-{label}-{y0}-{y1}-") as td:
            paths = download_block(y0, y1, Path(td))
            datasets = {v: Dataset(paths[v], "r") for v in VARIABLES}
            try:
                lat = get_coord(datasets["tmp"], ("lat", "latitude"))
                lon = get_coord(datasets["tmp"], ("lon", "longitude"))
                dates = dates_from(datasets["tmp"])
                if lat0 is None:
                    lat0, lon0 = lat, lon
                    shape = (12, len(lat0), len(lon0))
                    sums = {k: np.zeros(shape, dtype="float64") for k in ("tmp", "pre", "vpd")}
                    counts = {k: np.zeros(shape, dtype="uint16") for k in ("tmp", "pre", "vpd")}
                elif not np.allclose(lat, lat0) or not np.allclose(lon, lon0):
                    raise RuntimeError("CRU coordinate mismatch between blocks")

                for other in ("pre", "vap"):
                    olat = get_coord(datasets[other], ("lat", "latitude"))
                    olon = get_coord(datasets[other], ("lon", "longitude"))
                    if not np.allclose(olat, lat0) or not np.allclose(olon, lon0):
                        raise RuntimeError(f"Coordinate mismatch for {other}")
                    odates = dates_from(datasets[other])
                    if [(d.year, d.month) for d in odates] != [(d.year, d.month) for d in dates]:
                        raise RuntimeError(f"Time mismatch for {other}")

                tmpv = get_data_var(datasets["tmp"], "tmp")
                prev = get_data_var(datasets["pre"], "pre")
                vapv = get_data_var(datasets["vap"], "vap")
                for i, d in enumerate(dates):
                    year, month = int(d.year), int(d.month)
                    if year < start_year or year > end_year:
                        continue
                    mi = month - 1
                    tmp = np.ma.asarray(tmpv[i, :, :], dtype="float64")
                    pre = np.ma.asarray(prev[i, :, :], dtype="float64")
                    vap = np.ma.asarray(vapv[i, :, :], dtype="float64")
                    vpd = saturation_vapour_pressure_hpa(tmp) - vap
                    add_month(sums["tmp"], counts["tmp"], mi, tmp)
                    add_month(sums["pre"], counts["pre"], mi, pre)
                    add_month(sums["vpd"], counts["vpd"], mi, vpd)
            finally:
                for ds in datasets.values():
                    ds.close()

    if lat0 is None or lon0 is None:
        raise RuntimeError(f"No CRU data processed for {label}")

    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    fields = {k: mean_from(sums[k], counts[k]) for k in sums}
    with Dataset(out_path, "w", format="NETCDF4") as ds:
        ds.createDimension("month", 12)
        ds.createDimension("lat", len(lat0))
        ds.createDimension("lon", len(lon0))
        mv = ds.createVariable("month", "i1", ("month",))
        yv = ds.createVariable("lat", "f4", ("lat",))
        xv = ds.createVariable("lon", "f4", ("lon",))
        mv[:] = np.arange(1, 13, dtype="int8")
        yv[:] = lat0.astype("float32")
        xv[:] = lon0.astype("float32")
        mv.long_name = "calendar month"
        yv.units = "degrees_north"
        xv.units = "degrees_east"

        specs = {
            "tmp": ("degC", "mean temperature for calendar month"),
            "pre": ("mm month-1", "mean monthly precipitation total for calendar month"),
            "vpd": ("hPa", "mean monthly vapour pressure deficit for calendar month"),
        }
        for name, data in fields.items():
            v = ds.createVariable(
                name, "f4", ("month", "lat", "lon"),
                zlib=True, complevel=6, shuffle=True, fill_value=FILL,
                chunksizes=(1, min(180, len(lat0)), min(360, len(lon0))),
            )
            v[:] = data
            v.units, v.long_name = specs[name]

        ds.title = f"Climate Pulse CRU-TS v{VERSION} monthly context: {start_year}-{end_year}"
        ds.period_start = start_year
        ds.period_end = end_year
        ds.source_dataset = f"CRU-TS v{VERSION}"
        ds.source_resolution = "0.5 degree"
        ds.source_url = CRU_HOME
        ds.cru_reference = f"{CRU_REFERENCE}; {CRU_REFERENCE_DOI}"
        ds.vpd_definition = "VPD = SVP(T_monthly_mean) - CRU vap; VPD calculated per source month before across-year calendar-month averaging"
        ds.vpd_reference = f"{VPD_REFERENCE}; {VPD_REFERENCE_DOI}"
        ds.temporal_note = "This is a same-calendar-month climatological context product, not an observation for a current 2026 event month."
        ds.history = f"Generated {now_iso()} by scripts/prepare_cru_monthly_context.py"

    print(f"Wrote {out_path.name} ({out_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    if out_path.stat().st_size >= 95 * 1024 * 1024:
        raise RuntimeError(f"Monthly context file too large for normal GitHub contents: {out_path.stat().st_size} bytes")
    return out_path


def main() -> None:
    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    products = {}
    for label, (start, end) in PERIODS.items():
        path = build_period(label, start, end)
        products[label] = {
            "period": [start, end],
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size_bytes": path.stat().st_size,
        }

    meta = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "dataset": f"CRU-TS v{VERSION}",
        "resolution_degrees": 0.5,
        "purpose": "compact seasonal context without storing the full 1901-2025 monthly archive",
        "products": products,
        "variables": {
            "tmp": "same-calendar-month mean temperature, degC",
            "pre": "same-calendar-month mean precipitation total, mm/month",
            "vpd": "same-calendar-month mean VPD, hPa, derived month-by-month from CRU tmp and vap",
        },
        "comparison": {
            "reference_normal": [1981, 2010],
            "recent_period": [2016, 2025],
            "interpretation": "For an event occurring in month M, compare the recent mean for month M with the 1981-2010 normal for the same month. This controls for the seasonal cycle.",
            "not_event_month_observation": "CRU-TS v4.10 ends in 2025, so current 2026 event-month weather is not represented. A near-real-time source would be needed for a true event-month anomaly.",
        },
        "vpd": {
            "formula": "VPD = SVP - AVP; AVP = CRU vap; SVP = 6.1078*exp(a*T/(T+b)) hPa",
            "constants": {"T_ge_0C": {"a": 17.269, "b": 237.3}, "T_lt_0C": {"a": 21.875, "b": 265.5}},
            "reference": VPD_REFERENCE,
            "reference_url": VPD_REFERENCE_DOI,
            "caveat": "Monthly-mean temperature omits sub-daily and diurnal temperature variability; VPD here is long-term climate context, not high-frequency atmospheric dryness.",
        },
        "cru_reference": CRU_REFERENCE,
        "cru_reference_url": CRU_REFERENCE_DOI,
        "source_page": CRU_HOME,
    }
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "products": products}, indent=2))


if __name__ == "__main__":
    main()
