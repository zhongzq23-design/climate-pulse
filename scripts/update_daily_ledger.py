#!/usr/bin/env python3
"""Maintain a compact daily reporting ledger from the enriched Climate Pulse snapshot.

The raw 3x-daily event archive remains authoritative. This ledger is a derived,
report-friendly layer: it preserves exact source/derived metric values, merges
repeated observations of the same event within a UTC day, and stores a compact
reporting geometry for later weekly/monthly spatial deduplication.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
RUNTIME_DIR = ROOT / ".runtime" / "event_geometries"
DAILY_ROOT = ROOT / "data" / "history" / "daily"
TYPE_CODE = {"Flood": "FL", "Storm": "TC", "Wildfire": "WF", "Drought": "DR"}
ALERT_RANK = {"": 0, "Green": 1, "Orange": 2, "Red": 3}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "event"))[:160]


def finite_number(value: Any) -> float | int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    return int(n) if n.is_integer() else n


def alert_level(event: dict[str, Any]) -> str:
    explicit = str(event.get("alert_level") or "").strip().title()
    if explicit in ALERT_RANK:
        return explicit
    text = f"{event.get('source') or ''} {event.get('summary') or ''}".lower()
    for level in ("Red", "Orange", "Green"):
        if re.search(rf"\b{level.lower()}\b", text):
            return level
    return ""


def source_members(event: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if event.get("origin") and event.get("source_id") is not None:
        rows.append({"origin": str(event["origin"]), "source_id": str(event["source_id"])})
    for row in event.get("source_members") or []:
        if isinstance(row, dict) and row.get("origin") and row.get("source_id") is not None:
            rows.append({"origin": str(row["origin"]), "source_id": str(row["source_id"])})
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["origin"], row["source_id"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def stable_identity(event: dict[str, Any]) -> str:
    members = source_members(event)
    code = TYPE_CODE.get(str(event.get("type")), str(event.get("type") or "EV").upper())
    for origin in ("gdacs", "cems", "eonet"):
        ids = sorted(row["source_id"] for row in members if row["origin"].lower() == origin)
        if ids:
            return f"{origin}:{code}:{ids[0]}"
    seed = "|".join(sorted(f"{r['origin']}:{r['source_id']}" for r in members)) or str(event.get("id") or "unknown")
    return f"event:{code}:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def raw_metrics(event: dict[str, Any]) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    exposure = event.get("exposure") if isinstance(event.get("exposure"), dict) else {}
    for key, value in exposure.items():
        if key.startswith("population_") or key in {
            "estimated_population_exposed", "potential_gdp_exposure_proxy_usd",
            "mapped_footprint_area_km2", "crop_area_in_footprint_km2",
            "land_area_in_footprint_km2", "forest_area_in_footprint_km2",
            "forest_area_in_wildfire_footprint_km2",
        }:
            n = finite_number(value)
            if n is not None:
                out[key] = n
    n = finite_number(event.get("burned_area_ha"))
    if n is not None:
        out["burned_area_ha"] = n
    source_metrics = event.get("source_metrics") if isinstance(event.get("source_metrics"), dict) else {}
    for key in ("agricultural_drought_impact_area_km2",):
        n = finite_number(source_metrics.get(key))
        if n is not None:
            out[key] = n
    human = source_metrics.get("human_impact") if isinstance(source_metrics.get("human_impact"), dict) else {}
    for key in ("affected_population", "displaced_population", "fatalities", "food_insecurity_population"):
        n = finite_number(human.get(key))
        if n is not None:
            out[key] = n
    return out


def merge_max(existing: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(existing)
    for key, value in current.items():
        n = finite_number(value)
        old = finite_number(out.get(key))
        if n is not None and (old is None or float(n) > float(old)):
            out[key] = n
    return out


def round_coords(value: Any, digits: int = 4) -> Any:
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(x), digits) for x in value]
        return [round_coords(x, digits) for x in value]
    return value


def reporting_geometry(event_id: Any) -> dict[str, Any] | None:
    path = RUNTIME_DIR / f"{safe_name(event_id)}.json"
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        geom = shape(doc.get("geometry"))
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = make_valid(geom)
        if geom.is_empty:
            return None
        minx, miny, maxx, maxy = geom.bounds
        span = max(maxx - minx, maxy - miny)
        tolerance = min(0.01, max(0.0005, span / 1500.0))
        compact = geom.simplify(tolerance, preserve_topology=True)
        if compact.is_empty:
            compact = geom
        gj = mapping(compact)
        gj["coordinates"] = round_coords(gj.get("coordinates"), 4)
        return {
            "geometry": gj,
            "source": "runtime unsimplified mapped source footprint",
            "report_simplification_tolerance_degrees": round(tolerance, 6),
            "footprint_method": doc.get("footprint_method"),
            "geometry_qc": doc.get("geometry_qc"),
            "gdacs_event_type": doc.get("gdacs_event_type"),
            "gdacs_event_id": doc.get("gdacs_event_id"),
            "gdacs_episode_id": doc.get("gdacs_episode_id"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = load_json(LATEST, {})
    generated = str(snap.get("generated_at") or "")
    try:
        run_dt = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        run_dt = now_utc()
        generated = iso(run_dt)
    day_path = DAILY_ROOT / run_dt.strftime("%Y") / run_dt.strftime("%m") / f"{run_dt.strftime('%d')}.json"
    day_path.parent.mkdir(parents=True, exist_ok=True)
    day = load_json(day_path, {
        "schema_version": "1.0",
        "date_utc": run_dt.date().isoformat(),
        "first_run_at": generated,
        "last_run_at": generated,
        "run_count": 0,
        "source_runs": [],
        "raw_archive_authority": "data/events/archive (3x-daily enriched snapshots)",
        "events": {},
    })
    day["last_run_at"] = generated
    day["run_count"] = int(day.get("run_count", 0)) + 1
    runs = list(day.get("source_runs") or [])
    if generated not in runs:
        runs.append(generated)
    day["source_runs"] = sorted(runs)
    events_table = day.setdefault("events", {})

    for event in snap.get("canonical_events") or []:
        if not isinstance(event, dict):
            continue
        stable = stable_identity(event)
        prev = deepcopy(events_table.get(stable) or {})
        current_metrics = raw_metrics(event)
        level = alert_level(event)
        event_ids = sorted(set([*(prev.get("event_ids") or []), str(event.get("id") or "")]) - {""})
        rec = {
            "stable_id": stable,
            "event_ids": event_ids,
            "type": event.get("type"),
            "title": event.get("title"),
            "region": event.get("region"),
            "lat": event.get("lat"),
            "lon": event.get("lon"),
            "source_members": source_members(event),
            "first_observed_at": prev.get("first_observed_at") or generated,
            "last_observed_at": generated,
            "event_date_latest": event.get("event_date"),
            "alert_level_latest": level,
            "max_alert_level": prev.get("max_alert_level") or level,
            "raw_metrics_latest": current_metrics,
            "raw_metrics_max": merge_max(prev.get("raw_metrics_max") or {}, current_metrics),
            "metric_provenance": deepcopy((event.get("exposure") or {}).get("metric_provenance") or {}),
        }
        old_level = str(prev.get("max_alert_level") or "")
        if ALERT_RANK.get(level, 0) >= ALERT_RANK.get(old_level, 0):
            rec["max_alert_level"] = level or old_level
        geom = reporting_geometry(event.get("id"))
        if geom and "geometry" in geom:
            geom["captured_at"] = generated
            rec["reporting_footprint"] = geom
        elif prev.get("reporting_footprint"):
            rec["reporting_footprint"] = prev["reporting_footprint"]
        elif geom:
            rec["reporting_footprint_status"] = geom
        events_table[stable] = rec

    day["event_count"] = len(events_table)
    day_path.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "daily_ledger": str(day_path.relative_to(ROOT)),
        "date_utc": day["date_utc"],
        "run_count": day["run_count"],
        "event_count": day["event_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
