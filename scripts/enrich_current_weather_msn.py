#!/usr/bin/env python3
"""Probe current event weather through the official MSN Weather Connector bridge.

Climate Pulse cannot call the Power Automate / Logic Apps connector directly from
GitHub Actions without a Microsoft-side connection resource. This script expects
an HTTPS bridge endpoint created in Power Automate or Azure Logic Apps which uses
MSN Weather -> Get current weather (CurrentWeather) with Metric units.

The bridge receives:
    {"location": "<lat>,<lon>", "units": "Metric"}

It may return either:
1) the official MSN Weather connector CurrentWeather body; or
2) a simplified body containing temperature_c and dewpoint_c.

Current VPD is calculated from current temperature T and dewpoint Td:
    VPD = es(T) - es(Td)
where es follows the same piecewise saturation-vapour-pressure equation used by
Climate Pulse for CRU-derived VPD. This produces a point/current-condition VPD,
not a monthly anomaly and not event attribution.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "data" / "events" / "latest.json"
OUT_DIR = ROOT / "data" / "current_weather" / "msn"
OUT_PATH = OUT_DIR / "latest.json"

SOURCE = "Microsoft MSN Weather Connector"
OPERATION = "CurrentWeather"
VPD_RELEVANT_TYPES = {"Drought", "Wildfire", "Heat"}
DEFAULT_MIN_INTERVAL_SECONDS = 8.0  # connector limit: 8 calls / 60 s / connection


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def saturation_vapour_pressure_hpa(t_c: float) -> float:
    if t_c >= 0.0:
        a, b = 17.269, 237.3
    else:
        a, b = 21.875, 265.5
    return 6.1078 * math.exp((a * t_c) / (t_c + b))


def vpd_from_temp_dewpoint_hpa(t_c: float, td_c: float) -> tuple[float, float]:
    raw = saturation_vapour_pressure_hpa(t_c) - saturation_vapour_pressure_hpa(td_c)
    # Dew point should not exceed air temperature physically. Small negative values
    # can occur from provider rounding; expose raw value but display a non-negative VPD.
    return max(0.0, raw), raw


def nested(d: Any, *path: str) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def as_float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_bridge_response(body: dict[str, Any]) -> dict[str, Any]:
    # Simplified bridge response.
    t = as_float(body.get("temperature_c"))
    td = as_float(body.get("dewpoint_c"))
    created = body.get("provider_created") or body.get("created")
    plat = as_float(body.get("latitude"))
    plon = as_float(body.get("longitude"))
    location = body.get("location")

    # Raw official connector CurrentWeather response.
    if t is None:
        t = as_float(nested(body, "responses", "weather", "current", "temp"))
    if td is None:
        td = as_float(nested(body, "responses", "weather", "current", "dewPt"))
    if created is None:
        created = nested(body, "responses", "weather", "current", "created")
    if plat is None:
        plat = as_float(nested(body, "responses", "source", "coordinates", "lat"))
    if plon is None:
        plon = as_float(nested(body, "responses", "source", "coordinates", "lon"))
    if location is None:
        location = nested(body, "responses", "source", "location")

    unit = body.get("temperature_unit") or nested(body, "units", "temperature")
    if t is None or td is None:
        raise ValueError("Bridge response does not contain both current temperature and dew point")
    if unit and str(unit).lower() not in {"c", "°c", "celsius", "metric"}:
        raise ValueError(f"Expected metric/Celsius connector response, got temperature unit {unit!r}")

    vpd, raw = vpd_from_temp_dewpoint_hpa(t, td)
    return {
        "temperature_c": round(t, 3),
        "dewpoint_c": round(td, 3),
        "vpd_hpa": round(vpd, 3),
        "vpd_raw_hpa": round(raw, 3),
        "provider_created": created,
        "provider_location": location,
        "provider_lat": plat,
        "provider_lon": plon,
    }


def call_bridge(url: str, token: str | None, lat: float, lon: float, timeout: int = 30) -> dict[str, Any]:
    payload = json.dumps({"location": f"{lat:.5f},{lon:.5f}", "units": "Metric"}).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "ClimatePulse/0.6"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("Bridge returned a non-object JSON response")
    return parse_bridge_response(body)


def self_test() -> None:
    official = {
        "responses": {
            "weather": {"current": {"temp": 30.0, "dewPt": 20.0, "created": "2026-09-05T09:00:00Z"}},
            "source": {"coordinates": {"lat": 57.7, "lon": 11.97}, "location": "Gothenburg"},
        },
        "units": {"temperature": "C"},
    }
    x = parse_bridge_response(official)
    assert abs(x["vpd_hpa"] - 18.92) < 0.1, x
    simple = parse_bridge_response({"temperature_c": 25, "dewpoint_c": 15})
    assert 11.7 < simple["vpd_hpa"] < 12.0, simple
    print(json.dumps({"status": "self_test_ok", "official_shape": x, "simple_shape": simple}, indent=2))


def write_not_configured() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "provider": SOURCE,
        "operation": OPERATION,
        "status": "not_configured",
        "note": "Set MSN_WEATHER_BRIDGE_URL to a Power Automate or Logic Apps HTTPS bridge that invokes MSN Weather CurrentWeather.",
        "events": [],
    }, indent=2) + "\n", encoding="utf-8")


def run_live() -> None:
    url = os.environ.get("MSN_WEATHER_BRIDGE_URL", "").strip()
    token = os.environ.get("MSN_WEATHER_BRIDGE_TOKEN", "").strip() or None
    if not url:
        write_not_configured()
        print("MSN_WEATHER_BRIDGE_URL is not configured; wrote a non-active status file.")
        return

    snap = load_json(EVENTS_PATH, {})
    events = snap.get("events") if isinstance(snap, dict) else None
    if not isinstance(events, list):
        raise RuntimeError("data/events/latest.json has no events list")

    max_events = int(os.environ.get("MSN_WEATHER_MAX_EVENTS", "80"))
    min_interval = float(os.environ.get("MSN_WEATHER_MIN_INTERVAL_SECONDS", str(DEFAULT_MIN_INTERVAL_SECONDS)))
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    last_call = 0.0

    for event in events[:max_events]:
        try:
            lat, lon = float(event["lat"]), float(event["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        key = f"{lat:.5f},{lon:.5f}"
        try:
            if key not in cache:
                elapsed = time.monotonic() - last_call
                if last_call and elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
                cache[key] = call_bridge(url, token, lat, lon)
                last_call = time.monotonic()
            w = cache[key]
            rows.append({
                "event_id": event.get("id"),
                "event_type": event.get("type"),
                "title": event.get("title"),
                "requested_lat": lat,
                "requested_lon": lon,
                **w,
                "show_temperature": True,
                "show_vpd": event.get("type") in VPD_RELEVANT_TYPES,
            })
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            failures.append({"event_id": event.get("id"), "location": key, "error": f"{type(exc).__name__}: {str(exc)[:220]}"})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "provider": SOURCE,
        "operation": OPERATION,
        "status": "ready" if rows else "failed",
        "connector_limit": "8 calls per connection per 60 seconds",
        "variables": {
            "temperature_c": "current temperature returned by MSN Weather Connector",
            "dewpoint_c": "current dew point returned by MSN Weather Connector",
            "vpd_hpa": "max(0, es(T)-es(Td)); dew point is used to estimate actual vapour pressure",
        },
        "vpd_formula": "es=6.1078*exp(a*T/(T+b)) hPa; (a,b)=(17.269,237.3) for T>=0C and (21.875,265.5) for T<0C",
        "scientific_note": "Current weather is a point/current-condition product. It is not a CRU long-term metric, not a monthly anomaly, and not event attribution.",
        "events": rows,
        "failures": failures,
        "diagnostics": {"requested": min(len(events), max_events), "succeeded": len(rows), "failed": len(failures), "unique_locations": len(cache)},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "succeeded": len(rows), "failed": len(failures), "output": str(OUT_PATH.relative_to(ROOT))}, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        run_live()


if __name__ == "__main__":
    main()
