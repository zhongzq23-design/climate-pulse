#!/usr/bin/env python3
"""Attach annual CRU-TS v4.10 climate context to current Climate Pulse events.

Public climate context is annual-only:
* annual series for hazard-relevant variables, 1901-2025;
* 1901-1930 early baseline;
* 2016-2025 recent 10-year mean;
* change from the early baseline;
* ordinary least-squares linear trend for 1901-2025, reported per century.

No trend-significance test and no monthly anomaly are shown in the current
public view.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
ANNUAL_DIR = ROOT / "data" / "reference" / "climate" / "cru_ts_4.10" / "annual"
OUT_DIR = ROOT / "data" / "climate" / "event_timeseries"
INDEX = OUT_DIR / "index.json"

YEARS = list(range(1901, 2026))
EARLY = (1901, 1930)
RECENT_ANNUAL = (2016, 2025)
MAX_LAND_FALLBACK_KM = 500.0
TREND_VERSION = "ols-linear-v1"

VARIABLE_PROFILES = {
    "Drought": ["tmp", "pre", "vpd"],
    "Wildfire": ["tmp", "pre", "vpd"],
    "Heat": ["tmp", "pre", "vpd"],
    "Flood": ["tmp", "pre"],
    "Storm": ["tmp", "pre"],
    "Landslide": ["tmp", "pre"],
}
UNITS = {"tmp": "degC", "pre": "mm/year", "vpd": "hPa"}
LABELS = {"tmp": "Temperature", "pre": "Precipitation", "vpd": "VPD"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def scalar(v: Any) -> float | None:
    if np.ma.is_masked(v):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and abs(x) < 1e20 else None


def nearest_index(values: np.ndarray, x: float) -> int:
    return int(np.argmin(np.abs(values.astype("float64") - float(x))))


def resolve_land_cell(lat: np.ndarray, lon: np.ndarray, valid2d: np.ndarray, event_lat: float, event_lon: float) -> tuple[int, int, float, str] | None:
    yi = nearest_index(lat, event_lat)
    xi = nearest_index(lon, event_lon)
    if bool(valid2d[yi, xi]):
        d = haversine_km(event_lat, event_lon, float(lat[yi]), float(lon[xi]))
        return yi, xi, d, "nearest_grid_cell"
    best = None
    max_cells = 12
    y0, x0 = yi, xi
    for radius in range(1, max_cells + 1):
        y_min, y_max = max(0, y0 - radius), min(len(lat) - 1, y0 + radius)
        x_min, x_max = max(0, x0 - radius), min(len(lon) - 1, x0 + radius)
        candidates = []
        for y in range(y_min, y_max + 1):
            candidates.extend([(y, x_min), (y, x_max)])
        for x in range(x_min + 1, x_max):
            candidates.extend([(y_min, x), (y_max, x)])
        for y, x in candidates:
            if not bool(valid2d[y, x]):
                continue
            d = haversine_km(event_lat, event_lon, float(lat[y]), float(lon[x]))
            if best is None or d < best[2]:
                best = (y, x, d, "nearest_valid_land_cell")
        if best is not None and best[2] <= MAX_LAND_FALLBACK_KM:
            return best
    return None


def safe_name(event_id: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(event_id or "event"))[:160]


def valid_pairs(years: list[int], values: list[float | None]) -> tuple[np.ndarray, np.ndarray]:
    pairs = [(float(y), float(v)) for y, v in zip(years, values) if v is not None and math.isfinite(float(v))]
    if not pairs:
        return np.asarray([], dtype="float64"), np.asarray([], dtype="float64")
    return np.asarray([p[0] for p in pairs], dtype="float64"), np.asarray([p[1] for p in pairs], dtype="float64")


def mean_period(years: list[int], values: list[float | None], start: int, end: int) -> float | None:
    arr = [float(v) for y, v in zip(years, values) if start <= y <= end and v is not None and math.isfinite(float(v))]
    return float(np.mean(arr)) if arr else None


def linear_trend(years: list[int], values: list[float | None]) -> dict[str, float | None]:
    x, y = valid_pairs(years, values)
    if len(x) < 20:
        return {"slope_per_year": None, "intercept": None}
    slope, intercept = np.polyfit(x, y, 1)
    return {"slope_per_year": float(slope), "intercept": float(intercept)}


def diff_summary(var: str, baseline: float | None, recent: float | None) -> dict[str, float | None]:
    if baseline is None or recent is None:
        return {"absolute": None, "percent": None}
    absolute = float(recent - baseline)
    percent = float(absolute / baseline * 100.0) if var == "pre" and abs(baseline) > 1e-12 else None
    return {"absolute": absolute, "percent": percent}


def annual_summary(series: dict[str, list[float | None]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for var, values in series.items():
        early = mean_period(YEARS, values, *EARLY)
        recent = mean_period(YEARS, values, *RECENT_ANNUAL)
        fit = linear_trend(YEARS, values)
        slope_year = fit["slope_per_year"]
        trend_century = float(slope_year * 100.0) if slope_year is not None else None
        trend_pct = float(trend_century / early * 100.0) if var == "pre" and trend_century is not None and early not in (None, 0) else None
        out[var] = {
            "label": LABELS[var],
            "unit": UNITS[var],
            "baseline_1901_1930": early,
            "recent_2016_2025": recent,
            "change": diff_summary(var, early, recent),
            "trend_method": "ordinary least-squares linear trend",
            "linear_slope_per_year": slope_year,
            "linear_intercept": fit["intercept"],
            "trend_1901_2025_per_century": trend_century,
            "trend_percent_per_century": trend_pct,
        }
    return out


def build_context(event: dict[str, Any], variables: list[str], grid_lat: float, grid_lon: float, grid_distance_km: float, grid_method: str, annual_series: dict[str, list[float | None]]) -> dict[str, Any]:
    signature = f"cru4.10|{grid_lat:.2f}|{grid_lon:.2f}|{'-'.join(variables)}|annual1901-2025|{TREND_VERSION}"
    return {
        "schema_version": "2.1",
        "generated_at": now_iso(),
        "context_signature": signature,
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "dataset": "CRU-TS v4.10",
        "source_period": [1901, 2025],
        "reported_location": {"lat": event.get("lat"), "lon": event.get("lon")},
        "cru_grid": {"lat": grid_lat, "lon": grid_lon, "distance_km_from_reported_coordinate": round(grid_distance_km, 1), "selection_method": grid_method, "resolution_degrees": 0.5},
        "variable_profile": variables,
        "annual": {"years": YEARS, "series": annual_series, "summary": annual_summary(annual_series), "trend_method": "ordinary least-squares linear trend", "display_note": "Annual values retain long-term 1901-2025 context; the public chart also shows a 5-year moving mean for readability."},
        "scientific_note": "Climate context does not establish causal event attribution.",
        "vpd_note": "CRU VPD is derived from monthly-mean temperature and CRU vap before annual aggregation; it omits sub-daily/diurnal temperature variability.",
        "vpd_reference": "https://doi.org/10.1038/s41467-025-63672-z",
    }


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json is missing")
    first_annual = ANNUAL_DIR / "cru_ts4.10_1901_annual.nc"
    if not first_annual.exists():
        raise RuntimeError("CRU annual context is missing")
    snap = load_json(LATEST, {})
    events = snap.get("events") or []
    if not isinstance(events, list):
        raise RuntimeError("latest.json events is not a list")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with Dataset(first_annual, "r") as ds0:
        lat = np.asarray(ds0.variables["lat"][:], dtype="float64")
        lon = np.asarray(ds0.variables["lon"][:], dtype="float64")
        first_tmp = np.ma.asarray(ds0.variables["tmp"][:])
        valid = ~np.ma.getmaskarray(first_tmp) & np.isfinite(np.ma.filled(first_tmp, np.nan))
    pending: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for event in events:
        variables = VARIABLE_PROFILES.get(str(event.get("type")), ["tmp", "pre"])
        loc = resolve_land_cell(lat, lon, valid, float(event["lat"]), float(event["lon"]))
        if loc is None:
            event["climate_context"] = {"status": "unavailable", "reason": "No valid CRU land grid cell within 500 km of the reported coordinate", "variables": variables}
            continue
        yi, xi, dist, method = loc
        grid_lat, grid_lon = float(lat[yi]), float(lon[xi])
        file_name = safe_name(event.get("id")) + ".json"
        path = OUT_DIR / file_name
        signature = f"cru4.10|{grid_lat:.2f}|{grid_lon:.2f}|{'-'.join(variables)}|annual1901-2025|{TREND_VERSION}"
        existing = load_json(path, {}) if path.exists() else {}
        if existing.get("context_signature") == signature and existing.get("annual", {}).get("years") == YEARS:
            event["climate_context"] = {"status": "ready", "path": f"data/climate/event_timeseries/{file_name}", "variables": variables, "grid_lat": grid_lat, "grid_lon": grid_lon, "grid_distance_km": round(dist, 1)}
            entries.append({"event_id": event.get("id"), "path": event["climate_context"]["path"], "signature": signature})
            continue
        pending.append({"event": event, "variables": variables, "yi": yi, "xi": xi, "grid_lat": grid_lat, "grid_lon": grid_lon, "dist": dist, "method": method, "file_name": file_name, "signature": signature, "series": {v: [] for v in variables}})
    if pending:
        needed_vars = sorted({v for p in pending for v in p["variables"]})
        for year in YEARS:
            path = ANNUAL_DIR / f"cru_ts4.10_{year}_annual.nc"
            if not path.exists():
                raise RuntimeError(f"Missing annual CRU file: {path}")
            with Dataset(path, "r") as ds:
                arrays = {v: np.ma.asarray(ds.variables[v][:]) for v in needed_vars}
                for p in pending:
                    for v in p["variables"]:
                        p["series"][v].append(scalar(arrays[v][p["yi"], p["xi"]]))
        for p in pending:
            event = p["event"]
            context = build_context(event, p["variables"], p["grid_lat"], p["grid_lon"], p["dist"], p["method"], p["series"])
            out_path = OUT_DIR / p["file_name"]
            out_path.write_text(json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            event["climate_context"] = {"status": "ready", "path": f"data/climate/event_timeseries/{p['file_name']}", "variables": p["variables"], "grid_lat": p["grid_lat"], "grid_lon": p["grid_lon"], "grid_distance_km": round(p["dist"], 1)}
            entries.append({"event_id": event.get("id"), "path": event["climate_context"]["path"], "signature": p["signature"]})
    snap["schema_version"] = "1.4"
    snap.setdefault("monitor", {})["climate_context"] = {"dataset": "CRU-TS v4.10", "annual_period": [1901, 2025], "public_view": "annual_only", "trend_method": "ordinary least-squares linear trend", "significance": "not displayed", "note": "Monthly anomalies are not used in the current public view."}
    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INDEX.write_text(json.dumps({"schema_version": "2.1", "generated_at": now_iso(), "dataset": "CRU-TS v4.10", "trend_method": TREND_VERSION, "event_context_count": len(entries), "events": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "events": len(events), "contexts_ready": len(entries), "new_contexts": len(pending), "trend_method": TREND_VERSION}, indent=2))


if __name__ == "__main__":
    main()
