#!/usr/bin/env python3
"""Build annual CRU-TS v4.10 climate-context files for Climate Pulse.

Inputs (monthly, 0.5 degree, global land except Antarctica):
- tmp: monthly mean daily-mean temperature [degC]
- pre: monthly precipitation total [mm/month]
- vap: monthly actual vapour pressure [hPa]

Derived monthly VPD follows Zhong et al. (2025), Nature Communications:
    VPD = SVP - AVP
    SVP = 6.1078 * exp(a*T/(T+b))  [hPa]
where a=17.269,b=237.3 for T>=0 C and a=21.875,b=265.5 for T<0 C.
CRU `vap` is used directly as AVP.

Annual aggregation:
- tmp, vap, vpd: calendar-day-weighted mean of monthly values
- pre: sum of monthly totals [mm/year]

The monthly VPD calculation is intentionally documented as a coarse climate-
context product. Because saturation vapour pressure is nonlinear in temperature,
using monthly-mean temperature cannot reproduce the effect of sub-daily / diurnal
temperature variability. See Zhong et al. (2025):
https://doi.org/10.1038/s41467-025-63672-z

The script downloads decade-scale NetCDF chunks sequentially so the runner does
not need to hold the full 1901-2025 source files at once. It writes one compressed
NetCDF file per year for browser/backend lookup and a machine-readable manifest.
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import math
import os
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
ANNUAL_DIR = OUT_ROOT / "annual"
META_PATH = OUT_ROOT / "metadata.json"
MANIFEST_PATH = ANNUAL_DIR / "index.json"
README_PATH = OUT_ROOT / "README.md"

VERSION = "4.10"
RELEASE_FOLDER = "cruts.2604091129.v4.10"
START_YEAR = 1901
END_YEAR = 2025
RESOLUTION_DEG = 0.5
FILL = np.float32(9.96921e36)

CRU_HOME = "https://crudata.uea.ac.uk/cru/data/hrg/cru_ts_4.10/"
CRU_DATA_BASE = CRU_HOME + RELEASE_FOLDER + "/"
CRU_REFERENCE = "Harris I, Osborn TJ, Jones P and Lister D (2020), Scientific Data 7, 109"
CRU_REFERENCE_DOI = "https://doi.org/10.1038/s41597-020-0453-3"
VPD_REFERENCE = "Zhong Z, Chen HW, Dai A et al. (2025), Nature Communications 16, 8247"
VPD_REFERENCE_DOI = "https://doi.org/10.1038/s41467-025-63672-z"
USER_AGENT = "ClimatePulse/0.4 (+https://zhongzq23-design.github.io/climate-pulse/)"

BLOCKS = [
    (1901, 1910), (1911, 1920), (1921, 1930), (1931, 1940),
    (1941, 1950), (1951, 1960), (1961, 1970), (1971, 1980),
    (1981, 1990), (1991, 2000), (2001, 2010), (2011, 2020),
    (2021, 2025),
]
VARIABLES = ("tmp", "pre", "vap")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
                raise RuntimeError(f"Downloaded file is unexpectedly small: {dest.stat().st_size} bytes")
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
    # Defensive fallback for unusual capitalization / naming.
    for k, v in ds.variables.items():
        if k.lower() == name.lower():
            return v
    raise RuntimeError(f"Variable {name!r} not found. Available: {list(ds.variables)}")


def dates_from(ds: Dataset) -> list[Any]:
    t = ds.variables.get("time")
    if t is None:
        raise RuntimeError("CRU NetCDF has no time coordinate")
    units = getattr(t, "units", None)
    if not units:
        raise RuntimeError("CRU time coordinate has no units")
    cal = getattr(t, "calendar", "standard")
    return list(num2date(t[:], units=units, calendar=cal, only_use_cftime_datetimes=True))


def as_masked(var, indices: list[int]) -> np.ma.MaskedArray:
    arr = np.ma.asarray(var[indices, :, :], dtype="float64")
    # netCDF4 normally masks _FillValue/missing_value automatically; this also
    # guards against non-finite values that leak through source metadata.
    bad = ~np.isfinite(np.ma.filled(arr, np.nan))
    return np.ma.masked_where(bad, arr)


def saturation_vapour_pressure_hpa(tmp_c: np.ma.MaskedArray) -> np.ma.MaskedArray:
    warm = tmp_c >= 0.0
    a = np.ma.where(warm, 17.269, 21.875)
    b = np.ma.where(warm, 237.3, 265.5)
    return 6.1078 * np.ma.exp((a * tmp_c) / (tmp_c + b))


def weighted_mean_months(arr: np.ma.MaskedArray, weights: np.ndarray) -> np.ndarray:
    mask = np.ma.getmaskarray(arr)
    vals = np.ma.filled(arr, 0.0)
    finite = np.isfinite(vals)
    valid = (~mask) & finite
    w = weights[:, None, None].astype("float64")
    num = np.sum(np.where(valid, vals * w, 0.0), axis=0, dtype="float64")
    den = np.sum(np.where(valid, w, 0.0), axis=0, dtype="float64")
    out = np.full(arr.shape[1:], FILL, dtype="float32")
    ok = den > 0
    out[ok] = (num[ok] / den[ok]).astype("float32")
    return out


def sum_months(arr: np.ma.MaskedArray) -> np.ndarray:
    mask = np.ma.getmaskarray(arr)
    vals = np.ma.filled(arr, 0.0)
    finite = np.isfinite(vals)
    valid = (~mask) & finite
    num = np.sum(np.where(valid, vals, 0.0), axis=0, dtype="float64")
    count = np.sum(valid, axis=0)
    out = np.full(arr.shape[1:], FILL, dtype="float32")
    ok = count > 0
    out[ok] = num[ok].astype("float32")
    return out


def write_year(year: int, lat: np.ndarray, lon: np.ndarray, fields: dict[str, np.ndarray]) -> Path:
    ANNUAL_DIR.mkdir(parents=True, exist_ok=True)
    out = ANNUAL_DIR / f"cru_ts{VERSION}_{year}_annual.nc"
    with Dataset(out, "w", format="NETCDF4") as ds:
        ds.createDimension("lat", len(lat))
        ds.createDimension("lon", len(lon))
        yv = ds.createVariable("lat", "f4", ("lat",))
        xv = ds.createVariable("lon", "f4", ("lon",))
        yv[:] = lat.astype("float32")
        xv[:] = lon.astype("float32")
        yv.units = "degrees_north"
        xv.units = "degrees_east"
        yv.standard_name = "latitude"
        xv.standard_name = "longitude"

        specs = {
            "tmp": ("degC", "annual mean near-surface air temperature", "calendar-day-weighted mean of monthly CRU tmp"),
            "pre": ("mm year-1", "annual precipitation total", "sum of monthly CRU pre totals"),
            "vap": ("hPa", "annual mean actual vapour pressure", "calendar-day-weighted mean of monthly CRU vap"),
            "vpd": ("hPa", "annual mean vapour pressure deficit", "calendar-day-weighted mean of monthly VPD calculated before annual aggregation"),
        }
        for name, data in fields.items():
            v = ds.createVariable(name, "f4", ("lat", "lon"), zlib=True, complevel=5, shuffle=True, fill_value=FILL)
            v[:] = data
            units, long_name, method = specs[name]
            v.units = units
            v.long_name = long_name
            v.aggregation_method = method

        ds.title = f"Climate Pulse annual CRU-TS v{VERSION} climate context, {year}"
        ds.year = year
        ds.source_dataset = f"CRU-TS v{VERSION}"
        ds.source_resolution = "0.5 degree"
        ds.source_period = f"{START_YEAR}-{END_YEAR}"
        ds.source_url = CRU_HOME
        ds.cru_reference = f"{CRU_REFERENCE}; {CRU_REFERENCE_DOI}"
        ds.vpd_definition = "VPD = SVP(T_monthly_mean) - CRU vap; SVP in hPa uses piecewise constants a=17.269,b=237.3 for T>=0C and a=21.875,b=265.5 for T<0C"
        ds.vpd_reference = f"{VPD_REFERENCE}; {VPD_REFERENCE_DOI}"
        ds.vpd_temporal_caveat = "Monthly-mean temperature is used to calculate SVP before annual aggregation. This omits sub-daily/diurnal temperature variability and the nonlinearity of SVP with temperature; the result is a coarse climate-context VPD product, not a sub-daily VPD estimate."
        ds.vpd_negative_values = "Not clipped; the formula is preserved as calculated from monthly CRU tmp and vap."
        ds.annual_aggregation = "tmp/vap/vpd: calendar-day-weighted monthly mean; pre: sum of monthly totals"
        ds.license = "CRU-TS is made available under the Open Database License; individual contents under the Database Contents License, Attribution and Share-Alike. Attribution: Climatic Research Unit, University of East Anglia."
        ds.history = f"Generated {now_iso()} by Climate Pulse scripts/prepare_cru_annual.py"
    if out.stat().st_size >= 95 * 1024 * 1024:
        raise RuntimeError(f"Refusing to keep near-GitHub-limit file: {out} ({out.stat().st_size} bytes)")
    return out


def process_block(y0: int, y1: int) -> list[Path]:
    expected = [ANNUAL_DIR / f"cru_ts{VERSION}_{y}_annual.nc" for y in range(y0, y1 + 1)]
    if all(p.exists() and p.stat().st_size > 10_000 for p in expected):
        print(f"Block {y0}-{y1}: all annual outputs already exist; skipping download")
        return expected

    with tempfile.TemporaryDirectory(prefix=f"cru-{y0}-{y1}-") as td:
        td_path = Path(td)
        paths: dict[str, Path] = {}
        for var in VARIABLES:
            gz = td_path / f"{var}.nc.gz"
            nc = td_path / f"{var}.nc"
            url = source_url(var, y0, y1)
            print(f"Downloading {url}")
            download(url, gz)
            print(f"Decompressing {gz.name} ({gz.stat().st_size / 1024 / 1024:.1f} MiB compressed)")
            gunzip(gz, nc)
            gz.unlink()
            paths[var] = nc

        datasets = {v: Dataset(paths[v], "r") for v in VARIABLES}
        try:
            lat0 = get_coord(datasets["tmp"], ("lat", "latitude"))
            lon0 = get_coord(datasets["tmp"], ("lon", "longitude"))
            dates0 = dates_from(datasets["tmp"])
            for v in ("pre", "vap"):
                lat = get_coord(datasets[v], ("lat", "latitude"))
                lon = get_coord(datasets[v], ("lon", "longitude"))
                if lat.shape != lat0.shape or lon.shape != lon0.shape or not np.allclose(lat, lat0) or not np.allclose(lon, lon0):
                    raise RuntimeError(f"Coordinate mismatch between tmp and {v}")
                dates = dates_from(datasets[v])
                if [(d.year, d.month) for d in dates] != [(d.year, d.month) for d in dates0]:
                    raise RuntimeError(f"Time mismatch between tmp and {v}")

            tmpv = get_data_var(datasets["tmp"], "tmp")
            prev = get_data_var(datasets["pre"], "pre")
            vapv = get_data_var(datasets["vap"], "vap")
            outputs: list[Path] = []
            for year in range(y0, y1 + 1):
                out = ANNUAL_DIR / f"cru_ts{VERSION}_{year}_annual.nc"
                if out.exists() and out.stat().st_size > 10_000:
                    outputs.append(out)
                    continue
                idx = [i for i, d in enumerate(dates0) if int(d.year) == year]
                if len(idx) != 12:
                    raise RuntimeError(f"Expected 12 monthly records for {year}, found {len(idx)}")
                tmp = as_masked(tmpv, idx)
                pre = as_masked(prev, idx)
                vap = as_masked(vapv, idx)
                svp = saturation_vapour_pressure_hpa(tmp)
                vpd = svp - vap
                weights = np.asarray([calendar.monthrange(year, int(dates0[i].month))[1] for i in idx], dtype="float64")
                fields = {
                    "tmp": weighted_mean_months(tmp, weights),
                    "pre": sum_months(pre),
                    "vap": weighted_mean_months(vap, weights),
                    "vpd": weighted_mean_months(vpd, weights),
                }
                outputs.append(write_year(year, lat0, lon0, fields))
                print(f"Wrote {outputs[-1].name} ({outputs[-1].stat().st_size / 1024 / 1024:.2f} MiB)")
            return outputs
        finally:
            for ds in datasets.values():
                ds.close()


def write_docs(files: list[Path]) -> None:
    entries = []
    for path in sorted(files):
        year = int(path.stem.split("_")[-2])
        entries.append({
            "year": year,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    MANIFEST_PATH.write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "dataset": f"CRU-TS v{VERSION}",
        "years": [START_YEAR, END_YEAR],
        "resolution_degrees": RESOLUTION_DEG,
        "variables": {
            "tmp": {"units": "degC", "annual": "calendar-day-weighted mean"},
            "pre": {"units": "mm/year", "annual": "sum of monthly totals"},
            "vap": {"units": "hPa", "annual": "calendar-day-weighted mean"},
            "vpd": {"units": "hPa", "annual": "calendar-day-weighted mean of monthly VPD"},
        },
        "files": entries,
    }, indent=2) + "\n", encoding="utf-8")

    meta = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "dataset": f"CRU-TS v{VERSION}",
        "release_date": "2026-06-25",
        "source_period": [START_YEAR, END_YEAR],
        "spatial_resolution_degrees": RESOLUTION_DEG,
        "spatial_coverage": "global land areas except Antarctica",
        "source_page": CRU_HOME,
        "source_release_folder": RELEASE_FOLDER,
        "source_variables": {
            "tmp": "monthly average daily mean temperature, degC",
            "pre": "monthly precipitation total, mm/month",
            "vap": "monthly actual vapour pressure, hPa",
        },
        "annual_variables": {
            "tmp": "calendar-day-weighted annual mean, degC",
            "pre": "annual sum, mm/year",
            "vap": "calendar-day-weighted annual mean, hPa",
            "vpd": "calendar-day-weighted annual mean of monthly derived VPD, hPa",
        },
        "vpd": {
            "monthly_formula": "VPD = SVP - AVP; AVP = CRU vap; SVP = 6.1078*exp(a*T/(T+b)) hPa",
            "svp_constants": {"T_ge_0C": {"a": 17.269, "b": 237.3}, "T_lt_0C": {"a": 21.875, "b": 265.5}},
            "temperature_input": "CRU monthly mean tmp (degC)",
            "actual_vapour_pressure_input": "CRU monthly vap (hPa)",
            "negative_values_clipped": False,
            "reference": VPD_REFERENCE,
            "reference_url": VPD_REFERENCE_DOI,
            "caveat": "SVP is nonlinear in temperature. Calculating VPD from monthly-mean temperature omits sub-daily/diurnal temperature variability and can differ from an average of VPD calculated at sub-daily resolution. Use as long-term climate context rather than high-frequency atmospheric dryness.",
        },
        "cru_reference": CRU_REFERENCE,
        "cru_reference_url": CRU_REFERENCE_DOI,
        "license": "Open Database License / Database Contents License, Attribution and Share-Alike; attribute Climatic Research Unit, University of East Anglia",
        "annual_directory": str(ANNUAL_DIR.relative_to(ROOT)).replace("\\", "/"),
        "file_count": len(entries),
        "total_size_bytes": sum(x["size_bytes"] for x in entries),
        "manifest": str(MANIFEST_PATH.relative_to(ROOT)).replace("\\", "/"),
    }
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    README_PATH.write_text(f"""# CRU-TS v{VERSION} annual climate context\n\n"
"Climate Pulse stores a compact annual derivative of the current CRU-TS release for fast event-location climate context. The authoritative monthly CRU files remain external and are not copied into this repository.\n\n"
"## Coverage\n\n"
"- Source: **CRU-TS v{VERSION}**, Climatic Research Unit, University of East Anglia\n"
"- Period: **{START_YEAR}-{END_YEAR}**\n"
"- Grid: **0.5° × 0.5°**, global land except Antarctica\n"
"- One compressed NetCDF file per year\n\n"
"## Variables\n\n"
"- `tmp` — annual mean temperature (°C), day-weighted from monthly means\n"
"- `pre` — annual precipitation total (mm/year), sum of monthly totals\n"
"- `vap` — annual mean actual vapour pressure (hPa), day-weighted from monthly means\n"
"- `vpd` — annual mean vapour pressure deficit (hPa), calculated at monthly resolution first and then day-weighted to annual mean\n\n"
"## Monthly VPD calculation\n\n"
"`VPD = SVP - AVP`, with `AVP = CRU vap`. Saturation vapour pressure is:\n\n"
"`SVP = 6.1078 × exp(aT/(T+b))` hPa\n\n"
"where `(a,b)=(17.269,237.3)` for `T >= 0°C` and `(21.875,265.5)` for `T < 0°C`.\n\n"
"**Temporal-resolution caveat:** this uses monthly-mean temperature. Because SVP is nonlinear in temperature, it cannot reproduce sub-daily temperature variability or asymmetric daytime/nighttime warming. It is intended for long-term local climate context, not sub-daily VPD diagnosis. See [Zhong et al. (2025), Nature Communications 16, 8247]({VPD_REFERENCE_DOI}).\n\n"
"## References\n\n"
"- [CRU-TS v{VERSION} source]({CRU_HOME})\n"
"- [Harris et al. (2020), Scientific Data 7, 109]({CRU_REFERENCE_DOI})\n"
"- [Zhong et al. (2025), Nature Communications 16, 8247]({VPD_REFERENCE_DOI})\n\n"
"## Licence\n\n"
"CRU-TS is made available under the Open Database License, with individual contents under the Database Contents License, under Attribution and Share-Alike conditions. Attribution: **Climatic Research Unit, University of East Anglia**.\n"
""", encoding="utf-8")


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    ANNUAL_DIR.mkdir(parents=True, exist_ok=True)
    all_files: list[Path] = []
    for y0, y1 in BLOCKS:
        all_files.extend(process_block(y0, y1))
    all_files = sorted(set(all_files))
    if len(all_files) != END_YEAR - START_YEAR + 1:
        raise RuntimeError(f"Expected {END_YEAR - START_YEAR + 1} annual files, found {len(all_files)}")
    total = sum(p.stat().st_size for p in all_files)
    # Keep the repository comfortably below GitHub Pages/repository practical limits.
    if total >= 800 * 1024 * 1024:
        raise RuntimeError(f"Annual CRU derivative is unexpectedly large ({total / 1024 / 1024:.1f} MiB); refusing automatic commit")
    write_docs(all_files)
    print(json.dumps({
        "status": "ok",
        "dataset": f"CRU-TS v{VERSION}",
        "years": [START_YEAR, END_YEAR],
        "annual_files": len(all_files),
        "total_size_mib": round(total / 1024 / 1024, 2),
        "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
    }, indent=2))


if __name__ == "__main__":
    main()
