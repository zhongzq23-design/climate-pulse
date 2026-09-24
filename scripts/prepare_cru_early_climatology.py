#!/usr/bin/env python3
"""Build the compact 1901-1930 same-calendar-month CRU-TS v4.10 normal.

This creates a persistent 12-month climatology matching the existing
1981-2010 monthly product. Raw decadal CRU files are used only as source
inputs and are cached outside the repository.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import urllib.request
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

ROOT = Path(__file__).resolve().parents[1]
MONTHLY_DIR = ROOT / "data" / "reference" / "climate" / "cru_ts_4.10" / "monthly"
OUT = MONTHLY_DIR / "climatology_1901_1930.nc"
META = MONTHLY_DIR / "metadata.json"
CACHE = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse")) / "cru_ts_4.10" / "1901_1930"
BASE = "https://crudata.uea.ac.uk/cru/data/hrg/cru_ts_4.10/cruts.2604091129.v4.10"
DECADES = ((1901, 1910), (1911, 1920), (1921, 1930))
VARS = ("tmp", "pre", "vap")


def saturation_vapour_pressure_hpa(temp_c: np.ndarray) -> np.ndarray:
    a = np.where(temp_c >= 0.0, 17.269, 21.875)
    b = np.where(temp_c >= 0.0, 237.3, 265.5)
    return 6.1078 * np.exp(a * temp_c / (temp_c + b))


def source_url(var: str, start: int, end: int) -> str:
    name = f"cru_ts4.10.{start}.{end}.{var}.dat.nc.gz"
    return f"{BASE}/{var}/{name}"


def cached_nc(var: str, start: int, end: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    gz_path = CACHE / f"cru_ts4.10.{start}.{end}.{var}.dat.nc.gz"
    nc_path = gz_path.with_suffix("")
    if not gz_path.exists():
        req = urllib.request.Request(source_url(var, start, end), headers={"User-Agent": "Climate-Pulse/1.0"})
        with urllib.request.urlopen(req, timeout=120) as src, gz_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    if not nc_path.exists():
        with gzip.open(gz_path, "rb") as src, nc_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return nc_path


def month_years(ds: Dataset) -> list[tuple[int, int]]:
    t = ds.variables["time"]
    dates = num2date(
        t[:],
        units=t.units,
        calendar=getattr(t, "calendar", "standard"),
        only_use_cftime_datetimes=True,
    )
    return [(int(d.year), int(d.month)) for d in dates]


def update_metadata(size_bytes: int) -> None:
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    products = meta.setdefault("products", {})
    products["climatology_1901_1930"] = {
        "period": [1901, 1930],
        "path": "data/reference/climate/cru_ts_4.10/monthly/climatology_1901_1930.nc",
        "size_bytes": int(size_bytes),
    }
    provenance = meta.setdefault("product_provenance", {})
    provenance["climatology_1901_1930"] = {
        "source_release_folder": "cruts.2604091129.v4.10",
        "source_period": [1901, 1930],
        "source_variables": ["tmp", "pre", "vap"],
        "aggregation": "same-calendar-month mean across 1901-1930",
        "vpd_method": "VPD = SVP(CRU monthly mean tmp) - CRU monthly vap; negative numerical/data inconsistencies clipped to zero",
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build() -> None:
    if OUT.exists():
        update_metadata(OUT.stat().st_size)
        print(f"CRU early monthly normal already exists: {OUT}")
        return

    tmp_sum = pre_sum = vpd_sum = None
    tmp_n = pre_n = vpd_n = None
    lat = lon = None

    for start, end in DECADES:
        paths = {var: cached_nc(var, start, end) for var in VARS}
        with Dataset(paths["tmp"], "r") as dtmp, Dataset(paths["pre"], "r") as dpre, Dataset(paths["vap"], "r") as dvap:
            if lat is None:
                lat = np.asarray(dtmp.variables["lat"][:], dtype="float64")
                lon = np.asarray(dtmp.variables["lon"][:], dtype="float64")
                shape = (12, len(lat), len(lon))
                tmp_sum = np.zeros(shape, dtype="float64")
                pre_sum = np.zeros(shape, dtype="float64")
                vpd_sum = np.zeros(shape, dtype="float64")
                tmp_n = np.zeros(shape, dtype="uint16")
                pre_n = np.zeros(shape, dtype="uint16")
                vpd_n = np.zeros(shape, dtype="uint16")

            dates = month_years(dtmp)
            if dates != month_years(dpre) or dates != month_years(dvap):
                raise RuntimeError(f"CRU monthly time axes differ for {start}-{end}")

            for ti, (year, month) in enumerate(dates):
                if year < 1901 or year > 1930:
                    continue
                mi = month - 1
                t = np.ma.asarray(dtmp.variables["tmp"][ti, :, :], dtype="float64")
                p = np.ma.asarray(dpre.variables["pre"][ti, :, :], dtype="float64")
                vap = np.ma.asarray(dvap.variables["vap"][ti, :, :], dtype="float64")

                t_data = np.ma.filled(t, np.nan)
                p_data = np.ma.filled(p, np.nan)
                vap_data = np.ma.filled(vap, np.nan)
                t_ok = np.isfinite(t_data) & (np.abs(t_data) < 1e20)
                p_ok = np.isfinite(p_data) & (np.abs(p_data) < 1e20)
                v_ok = t_ok & np.isfinite(vap_data) & (np.abs(vap_data) < 1e20)

                tmp_sum[mi][t_ok] += t_data[t_ok]
                tmp_n[mi][t_ok] += 1
                pre_sum[mi][p_ok] += p_data[p_ok]
                pre_n[mi][p_ok] += 1
                if np.any(v_ok):
                    vpd = np.maximum(0.0, saturation_vapour_pressure_hpa(t_data) - vap_data)
                    vpd_sum[mi][v_ok] += vpd[v_ok]
                    vpd_n[mi][v_ok] += 1

    if lat is None or lon is None:
        raise RuntimeError("No CRU source data were read")

    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    fill = np.float32(-9999.0)
    with Dataset(OUT, "w", format="NETCDF4") as out:
        out.createDimension("month", 12)
        out.createDimension("lat", len(lat))
        out.createDimension("lon", len(lon))
        vm = out.createVariable("month", "i2", ("month",))
        vlat = out.createVariable("lat", "f4", ("lat",))
        vlon = out.createVariable("lon", "f4", ("lon",))
        vm[:] = np.arange(1, 13, dtype="int16")
        vlat[:] = lat.astype("float32")
        vlon[:] = lon.astype("float32")
        vlat.units = "degrees_north"
        vlon.units = "degrees_east"

        for name, sums, counts, units in (
            ("tmp", tmp_sum, tmp_n, "degC"),
            ("pre", pre_sum, pre_n, "mm/month"),
            ("vpd", vpd_sum, vpd_n, "hPa"),
        ):
            var = out.createVariable(
                name,
                "f4",
                ("month", "lat", "lon"),
                zlib=True,
                complevel=4,
                fill_value=fill,
            )
            mean = np.divide(sums, counts, out=np.full_like(sums, np.nan), where=counts > 0)
            var[:] = np.ma.masked_invalid(mean).astype("float32")
            var.units = units

        out.dataset = "CRU-TS v4.10"
        out.period = "1901-1930"
        out.description = "Same-calendar-month 30-year climate normal for Climate Pulse"
        out.source_release = "cruts.2604091129.v4.10"
        out.vpd_method = "VPD = SVP(tmp) - vap; phase-dependent SVP constants match Climate Pulse CRU processing"

    update_metadata(OUT.stat().st_size)
    print(f"Built {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
