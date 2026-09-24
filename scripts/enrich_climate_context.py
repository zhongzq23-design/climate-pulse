#!/usr/bin/env python3
"""Attach same-month climate normals and a recent seven-day IFS mean.

Historical side:
* CRU-TS v4.10, 0.5 degree land grid;
* 1901-1930 and 1981-2010 same-calendar-month normals;
* precipitation converted from mm/month to climatological mm/day.

Recent side:
* ECMWF IFS Near-Realtime forecast from Google Earth Engine;
* seven consecutive, fully elapsed 0-24 h forecast windows from the same daily
  cycle hour as the latest complete window;
* temperature is the mean of seven complete daily means, with each daily mean
  trapezoidally time-weighted from 3-hourly forecast steps;
* VPD is calculated for each day from that day's mean temperature and mean
  actual vapour pressure (derived from 3-hourly dew-point temperature), then
  averaged across the seven days;
* precipitation is the sum of seven daily 0-24 h accumulations divided by 7,
  reported as mm/day.

The recent values are short-range model-forecast context, not observations.
"""
from __future__ import annotations

import calendar
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
MONTHLY_DIR = ROOT / "data" / "reference" / "climate" / "cru_ts_4.10" / "monthly"
EARLY_PATH = MONTHLY_DIR / "climatology_1901_1930.nc"
MODERN_PATH = MONTHLY_DIR / "climatology_1981_2010.nc"
OUT_DIR = ROOT / "data" / "climate" / "event_timeseries"
INDEX = OUT_DIR / "index.json"
MAX_LAND_FALLBACK_KM = 500.0
SCHEMA_VERSION = "3.1"
IFS_COLLECTION = "ECMWF/NRT_FORECAST/IFS/OPER"
IFS_STEPS = tuple(range(0, 25, 3))
RECENT_DAYS = 7
DAY_MS = 24 * 3600 * 1000

VARIABLE_PROFILES = {
    "Drought": ["tmp", "pre", "vpd"],
    "Wildfire": ["tmp", "pre", "vpd"],
    "Heat": ["tmp", "pre", "vpd"],
    "Flood": ["tmp", "pre"],
    "Storm": ["tmp", "pre"],
    "Landslide": ["tmp", "pre"],
}
UNITS = {"tmp": "degC", "pre": "mm/day", "vpd": "hPa"}
LABELS = {"tmp": "Temperature", "pre": "Precipitation", "vpd": "VPD"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return iso_utc(now_utc())


def from_ms(ms: int | float) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def resolve_land_cell(
    lat: np.ndarray,
    lon: np.ndarray,
    valid2d: np.ndarray,
    event_lat: float,
    event_lon: float,
) -> tuple[int, int, float, str] | None:
    yi = nearest_index(lat, event_lat)
    xi = nearest_index(lon, event_lon)
    if bool(valid2d[yi, xi]):
        d = haversine_km(event_lat, event_lon, float(lat[yi]), float(lon[xi]))
        return yi, xi, d, "nearest_grid_cell"

    best = None
    for radius in range(1, 13):
        y_min, y_max = max(0, yi - radius), min(len(lat) - 1, yi + radius)
        x_min, x_max = max(0, xi - radius), min(len(lon) - 1, xi + radius)
        candidates: list[tuple[int, int]] = []
        for y in range(y_min, y_max + 1):
            candidates.extend(((y, x_min), (y, x_max)))
        for x in range(x_min + 1, x_max):
            candidates.extend(((y_min, x), (y_max, x)))
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


def period_mean_days(start: int, end: int, month: int) -> float:
    return float(np.mean([calendar.monthrange(y, month)[1] for y in range(start, end + 1)]))


def read_normal(
    path: Path,
    month: int,
    yi: int,
    xi: int,
    variables: list[str],
    period: tuple[int, int],
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    with Dataset(path, "r") as ds:
        month_values = (
            np.asarray(ds.variables["month"][:], dtype="int16")
            if "month" in ds.variables
            else np.arange(1, 13, dtype="int16")
        )
        hits = np.where(month_values == month)[0]
        if not len(hits):
            raise RuntimeError(f"Month {month} not found in {path}")
        mi = int(hits[0])
        for var in variables:
            if var not in ds.variables:
                values[var] = None
                continue
            val = scalar(ds.variables[var][mi, yi, xi])
            if var == "pre" and val is not None:
                val = float(val / period_mean_days(period[0], period[1], month))
            values[var] = val
    return values


def _svp_image(ee: Any, temp: Any) -> Any:
    a = temp.gte(0).multiply(17.269).add(temp.lt(0).multiply(21.875))
    b = temp.gte(0).multiply(237.3).add(temp.lt(0).multiply(265.5))
    return temp.multiply(a).divide(temp.add(b)).exp().multiply(6.1078)


def init_earth_engine() -> Any:
    import ee

    project = (os.environ.get("EE_PROJECT") or "").strip() or None
    key_json = os.environ.get("EE_SERVICE_ACCOUNT_JSON")
    key_file = (os.environ.get("EE_SERVICE_ACCOUNT_KEY_FILE") or "").strip() or None
    temp_name = None
    try:
        if key_json:
            parsed = json.loads(key_json)
            project = project or parsed.get("project_id")
            fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(parsed, fh)
            fh.close()
            temp_name = fh.name
            key_file = temp_name
        if key_file:
            key = json.loads(Path(key_file).read_text(encoding="utf-8"))
            service_account = key.get("client_email")
            if not service_account:
                raise RuntimeError("Earth Engine service-account JSON has no client_email")
            credentials = ee.ServiceAccountCredentials(service_account, key_file)
            ee.Initialize(credentials, project=project or key.get("project_id"))
        else:
            ee.Initialize(project=project)
        return ee
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _daily_ifs_metric_image(ee: Any, base: Any, cycle_ms: int, variables: set[str]) -> Any:
    cycle = base.filter(ee.Filter.eq("creation_time", int(cycle_ms)))
    weight_total = 0.0
    tmp_parts = []
    vap_parts = []

    for step in IFS_STEPS:
        image = ee.Image(cycle.filter(ee.Filter.eq("forecast_hours", step)).first())
        weight = 0.5 if step in (0, 24) else 1.0
        weight_total += weight
        if "tmp" in variables or "vpd" in variables:
            temp = image.select("temperature_2m_sfc")
            tmp_parts.append(temp.multiply(weight))
        if "vpd" in variables:
            dew = image.select("dewpoint_temperature_2m_sfc")
            vap_parts.append(_svp_image(ee, dew).multiply(weight))

    temp_mean = None
    if "tmp" in variables or "vpd" in variables:
        temp_mean = ee.ImageCollection.fromImages(tmp_parts).sum().divide(weight_total)

    out = None
    if "tmp" in variables:
        out = temp_mean.rename("tmp")
    if "vpd" in variables:
        vap_mean = ee.ImageCollection.fromImages(vap_parts).sum().divide(weight_total)
        vpd = _svp_image(ee, temp_mean).subtract(vap_mean).max(0).rename("vpd")
        out = vpd if out is None else out.addBands(vpd)
    if "pre" in variables:
        img0 = ee.Image(cycle.filter(ee.Filter.eq("forecast_hours", 0)).first())
        img24 = ee.Image(cycle.filter(ee.Filter.eq("forecast_hours", 24)).first())
        pre = (
            img24.select("total_precipitation_sfc")
            .subtract(img0.select("total_precipitation_sfc"))
            .max(0)
            .multiply(1000.0)
            .rename("pre")
        )
        out = pre if out is None else out.addBands(pre)

    if out is None:
        raise RuntimeError("No IFS variables requested")
    return out


def recent_7day_ifs_image(
    points: list[dict[str, Any]],
    variables: set[str],
    reference_now: datetime,
) -> tuple[dict[str, Any], dict[str, dict[str, float | None]]]:
    ee = init_earth_engine()
    now_ms = int(reference_now.timestamp() * 1000)
    recent_floor = now_ms - 10 * DAY_MS
    base = ee.ImageCollection(IFS_COLLECTION).filter(ee.Filter.eq("model", "ifs"))

    eligible = (
        base.filter(ee.Filter.eq("forecast_hours", 24))
        .filter(ee.Filter.lte("forecast_time", now_ms))
        .filter(ee.Filter.gte("creation_time", recent_floor))
        .sort("creation_time", False)
    )
    first = eligible.first()
    latest_cycle_ms = first.get("creation_time").getInfo()
    latest_end_ms = first.get("forecast_time").getInfo()
    if latest_cycle_ms is None or latest_end_ms is None:
        raise RuntimeError("No fully elapsed ECMWF IFS 0-24 h forecast window is available")

    cycle_mss = [int(latest_cycle_ms) - d * DAY_MS for d in range(RECENT_DAYS)]
    available = set(
        int(x)
        for x in (
            base.filter(ee.Filter.eq("forecast_hours", 24))
            .filter(ee.Filter.inList("creation_time", cycle_mss))
            .aggregate_array("creation_time")
            .getInfo()
            or []
        )
    )
    missing = [x for x in cycle_mss if x not in available]
    if missing:
        missing_iso = ", ".join(iso_utc(from_ms(x)) for x in missing)
        raise RuntimeError(f"IFS recent-7-day series is incomplete; missing daily cycles: {missing_iso}")

    cycle_mss = sorted(cycle_mss)
    daily_images = [_daily_ifs_metric_image(ee, base, cycle_ms, variables) for cycle_ms in cycle_mss]
    out = ee.ImageCollection.fromImages(daily_images).mean()

    features = [
        ee.Feature(
            ee.Geometry.Point([float(p["grid_lon"]), float(p["grid_lat"])]),
            {"sample_key": p["sample_key"]},
        )
        for p in points
    ]
    info = out.sampleRegions(
        collection=ee.FeatureCollection(features),
        scale=28000,
        geometries=False,
    ).getInfo()

    samples: dict[str, dict[str, float | None]] = {}
    for feat in info.get("features", []):
        props = feat.get("properties", {})
        key = str(props.get("sample_key", ""))
        if not key:
            continue
        samples[key] = {var: scalar(props.get(var)) for var in variables}

    start_dt = from_ms(cycle_mss[0])
    end_dt = from_ms(int(latest_end_ms))
    meta = {
        "status": "ready",
        "source": "ECMWF IFS Near-Realtime forecast via Google Earth Engine",
        "dataset_id": IFS_COLLECTION,
        "window_start": iso_utc(start_dt),
        "window_end": iso_utc(end_dt),
        "day_count": RECENT_DAYS,
        "daily_cycle_times": [iso_utc(from_ms(x)) for x in cycle_mss],
        "forecast_steps_hours": list(IFS_STEPS),
        "temperature_aggregation": (
            "mean of seven complete daily IFS 0-24 h temperature means; "
            "each daily mean is a 3-hourly trapezoidal time-weighted mean"
        ),
        "vpd_aggregation": (
            "mean of seven daily VPD values; each day uses SVP(daily mean temperature) "
            "minus daily mean actual vapour pressure derived from 3-hourly dew-point temperature"
        ),
        "precipitation_aggregation": (
            "sum of seven daily 0-24 h accumulated precipitation totals divided by 7"
        ),
        "resolution_degrees": 0.25,
    }
    return meta, samples


def period_record(
    key: str,
    label: str,
    period: tuple[int, int],
    month: int,
    values: dict[str, float | None],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "kind": "same_calendar_month_climatology",
        "source": "CRU-TS v4.10",
        "period": list(period),
        "n_years": period[1] - period[0] + 1,
        "month": month,
        "values": values,
    }


def recent_record(meta: dict[str, Any], values: dict[str, float | None] | None) -> dict[str, Any]:
    return {
        "key": "recent_7d",
        "label": "Recent 7-day mean",
        "kind": "recent_complete_ifs_daily_mean_7d",
        **meta,
        "values": values or {},
    }


def build_context(
    event: dict[str, Any],
    variables: list[str],
    grid_lat: float,
    grid_lon: float,
    grid_distance_km: float,
    grid_method: str,
    month: int,
    early_values: dict[str, float | None],
    modern_values: dict[str, float | None],
    recent_meta: dict[str, Any],
    recent_values: dict[str, float | None] | None,
) -> dict[str, Any]:
    signature = (
        f"climate-compare-v2|{grid_lat:.2f}|{grid_lon:.2f}|{'-'.join(variables)}|"
        f"m{month:02d}|1901-1930|1981-2010|recent7d"
    )
    periods = [
        period_record("1901_1930", "1901–1930", (1901, 1930), month, early_values),
        period_record("1981_2010", "1981–2010", (1981, 2010), month, modern_values),
        recent_record(recent_meta, recent_values),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "context_signature": signature,
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "reported_location": {"lat": event.get("lat"), "lon": event.get("lon")},
        "comparison_location": {
            "lat": grid_lat,
            "lon": grid_lon,
            "distance_km_from_reported_coordinate": round(grid_distance_km, 1),
            "selection_method": grid_method,
            "historical_resolution_degrees": 0.5,
        },
        "variable_profile": variables,
        "comparison": {
            "month": month,
            "month_label": calendar.month_name[month],
            "units": {v: UNITS[v] for v in variables},
            "labels": {v: LABELS[v] for v in variables},
            "periods": periods,
            "precipitation_note": (
                "Historical precipitation is the same-calendar-month CRU mean monthly total "
                "divided by the mean number of calendar days in that period. Recent precipitation "
                "is the mean daily rate over seven complete IFS 0-24 h windows. Both are shown as mm/day."
            ),
            "recent_note": (
                "Recent 7-day values are means across seven consecutive, fully elapsed daily IFS "
                "short-range forecast windows from the same UTC cycle hour, not observations."
            ),
        },
        "scientific_note": (
            "Climate comparison provides context and does not establish causal event attribution."
        ),
        "vpd_note": (
            "Historical CRU VPD uses monthly-mean temperature and actual vapour pressure. "
            "Recent IFS VPD is calculated for each complete day from daily mean temperature "
            "and daily mean actual vapour pressure derived from 3-hourly dew point, then averaged "
            "across seven days."
        ),
        "vpd_reference": "https://doi.org/10.1038/s41467-025-63672-z",
    }


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json is missing")
    if not EARLY_PATH.exists():
        raise RuntimeError(
            "Missing 1901-1930 monthly climatology; run scripts/prepare_cru_early_climatology.py"
        )
    if not MODERN_PATH.exists():
        raise RuntimeError("Missing 1981-2010 monthly climatology")

    snap = load_json(LATEST, {})
    events = snap.get("events") or snap.get("canonical_events") or []
    if not isinstance(events, list):
        raise RuntimeError("latest.json events/canonical_events is not a list")

    with Dataset(MODERN_PATH, "r") as ds0:
        lat = np.asarray(ds0.variables["lat"][:], dtype="float64")
        lon = np.asarray(ds0.variables["lon"][:], dtype="float64")
        first_tmp = np.ma.asarray(ds0.variables["tmp"][0, :, :])
        valid = ~np.ma.getmaskarray(first_tmp) & np.isfinite(
            np.ma.filled(first_tmp, np.nan)
        )

    reference_now = now_utc()
    month = reference_now.month
    resolved: list[dict[str, Any]] = []
    for event in events:
        try:
            event_lat, event_lon = float(event["lat"]), float(event["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        variables = VARIABLE_PROFILES.get(str(event.get("type")), ["tmp", "pre"])
        loc = resolve_land_cell(lat, lon, valid, event_lat, event_lon)
        if loc is None:
            event["climate_context"] = {
                "status": "unavailable",
                "reason": "No valid CRU land grid cell within 500 km of the reported coordinate",
                "variables": variables,
            }
            continue

        yi, xi, dist, method = loc
        resolved.append(
            {
                "event": event,
                "variables": variables,
                "yi": yi,
                "xi": xi,
                "grid_lat": float(lat[yi]),
                "grid_lon": float(lon[xi]),
                "dist": dist,
                "method": method,
                "file_name": safe_name(event.get("id")) + ".json",
                "sample_key": safe_name(event.get("id")),
            }
        )

    all_vars = {v for p in resolved for v in p["variables"]}
    recent_meta: dict[str, Any]
    recent_samples: dict[str, dict[str, float | None]] = {}
    try:
        recent_meta, recent_samples = recent_7day_ifs_image(
            resolved, all_vars, reference_now
        )
        month = datetime.fromisoformat(
            recent_meta["window_end"].replace("Z", "+00:00")
        ).month
    except Exception as exc:
        print(f"IFS recent-7-day enrichment unavailable: {type(exc).__name__}: {exc}")
        recent_meta = {
            "status": "unavailable",
            "source": "ECMWF IFS Near-Realtime forecast via Google Earth Engine",
            "dataset_id": IFS_COLLECTION,
            "day_count": RECENT_DAYS,
            "reason": (
                "Recent 7-day IFS enrichment is temporarily unavailable; Earth Engine "
                "authentication, data access, or one of the required daily cycles is unavailable."
            ),
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for p in resolved:
        variables = p["variables"]
        early_values = read_normal(
            EARLY_PATH, month, p["yi"], p["xi"], variables, (1901, 1930)
        )
        modern_values = read_normal(
            MODERN_PATH, month, p["yi"], p["xi"], variables, (1981, 2010)
        )
        recent_values = (
            recent_samples.get(p["sample_key"])
            if recent_meta.get("status") == "ready"
            else None
        )
        context = build_context(
            p["event"],
            variables,
            p["grid_lat"],
            p["grid_lon"],
            p["dist"],
            p["method"],
            month,
            early_values,
            modern_values,
            recent_meta,
            recent_values,
        )
        out_path = OUT_DIR / p["file_name"]
        out_path.write_text(
            json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        p["event"]["climate_context"] = {
            "status": "ready",
            "path": f"data/climate/event_timeseries/{p['file_name']}",
            "variables": variables,
            "grid_lat": p["grid_lat"],
            "grid_lon": p["grid_lon"],
            "grid_distance_km": round(p["dist"], 1),
            "recent_status": recent_meta.get("status"),
        }
        entries.append(
            {
                "event_id": p["event"].get("id"),
                "path": p["event"]["climate_context"]["path"],
                "signature": context["context_signature"],
            }
        )

    snap["schema_version"] = "1.5"
    snap.setdefault("monitor", {})["climate_context"] = {
        "historical_dataset": "CRU-TS v4.10",
        "early_normal": [1901, 1930],
        "modern_normal": [1981, 2010],
        "month": month,
        "recent_dataset": IFS_COLLECTION,
        "recent_window_status": recent_meta.get("status"),
        "recent_days": RECENT_DAYS,
        "public_view": "same_month_normals_vs_recent_7day_ifs_mean",
        "note": (
            "Recent IFS values are seven-day short-range model-forecast context, "
            "not observations or causal event attribution."
        ),
    }
    LATEST.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    INDEX.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": now_iso(),
                "historical_dataset": "CRU-TS v4.10",
                "recent_dataset": IFS_COLLECTION,
                "comparison_month": month,
                "recent_window": recent_meta,
                "event_context_count": len(entries),
                "events": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "events": len(events),
                "contexts_ready": len(entries),
                "comparison_month": month,
                "recent_window_status": recent_meta.get("status"),
                "recent_window_start": recent_meta.get("window_start"),
                "recent_window_end": recent_meta.get("window_end"),
                "recent_days": RECENT_DAYS,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
