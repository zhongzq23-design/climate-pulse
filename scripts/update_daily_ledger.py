#!/usr/bin/env python3
"""Maintain Climate Pulse daily reporting ledger with report-integrity metadata.

The 3x-daily enriched event archive remains authoritative. This compact daily
ledger preserves exact metrics, stable event identity, lifecycle/temporal fields,
public-significance state and reporting-geometry semantics for later weekly and
monthly aggregation.
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
LIFECYCLE = ROOT / "data" / "events" / "lifecycle.json"
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


def parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def min_iso(*values: Any) -> str | None:
    rows = [(parse_dt(v), str(v)) for v in values if v]
    rows = [(d, s) for d, s in rows if d]
    return min(rows, key=lambda x: x[0])[1] if rows else None


def max_iso(*values: Any) -> str | None:
    rows = [(parse_dt(v), str(v)) for v in values if v]
    rows = [(d, s) for d, s in rows if d]
    return max(rows, key=lambda x: x[0])[1] if rows else None


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


def geometry_semantics(event_type: str, footprint_method: Any) -> dict[str, str]:
    method = str(footprint_method or "").lower()
    if event_type == "Wildfire":
        return {"grade": "exposure_grade", "role": "burned_fire_perimeter",
                "interpretation": "Mapped wildfire perimeter suitable for spatial exposure aggregation; exposure does not mean harm."}
    if event_type == "Storm":
        return {"grade": "exposure_grade", "role": "tropical_cyclone_hazard_zone",
                "interpretation": "Mapped cyclone hazard zone (for example wind/impact footprint) suitable for spatial exposure aggregation."}
    if event_type == "Flood":
        return {"grade": "context_grade", "role": "reported_flood_event_area",
                "interpretation": "GDACS reported flood affected/event area; not observed inundation extent and excluded from cross-hazard exposure headline."}
    if event_type == "Drought":
        return {"grade": "risk_grade", "role": "drought_risk_impact_area",
                "interpretation": "Mapped drought risk/impact area; used for risk/crop context rather than direct human-exposure headline."}
    return {"grade": "context_grade", "role": "mapped_event_context",
            "interpretation": f"Context geometry ({method or 'unspecified method'})."}


def reporting_geometry(event: dict[str, Any]) -> dict[str, Any] | None:
    path = RUNTIME_DIR / f"{safe_name(event.get('id'))}.json"
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
        method = doc.get("footprint_method")
        return {
            "geometry": gj,
            "source": "runtime unsimplified mapped source footprint",
            "report_simplification_tolerance_degrees": round(tolerance, 6),
            "footprint_method": method,
            "geometry_semantics": geometry_semantics(str(event.get("type") or ""), method),
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


def lifecycle_keys(event: dict[str, Any]) -> list[str]:
    code = TYPE_CODE.get(str(event.get("type")), str(event.get("type") or "EV").upper())
    keys = [str(event.get("id") or "")]
    for row in source_members(event):
        origin, sid = row["origin"].lower(), row["source_id"]
        if origin == "gdacs":
            keys.append(f"gdacs-{code}-{sid}")
        elif origin in {"eonet", "cems"}:
            keys.append(f"{origin}-{sid}")
    return [k for k in dict.fromkeys(keys) if k]


def lifecycle_snapshot(event: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    table = state.get("events") if isinstance(state.get("events"), dict) else {}
    rows = [table[k] for k in lifecycle_keys(event) if isinstance(table.get(k), dict)]
    if not rows:
        return {}
    first = None
    last = None
    observations = 0
    for row in rows:
        first = min_iso(first, row.get("first_seen"))
        last = max_iso(last, row.get("last_seen"))
        observations += int(row.get("observations", 0) or 0)
    return {"first_seen": first, "last_seen": last, "source_observations": observations}


def public_significant(event: dict[str, Any]) -> bool:
    if "display_eligible" in event:
        return bool(event.get("display_eligible"))
    return True


def temporal_fields(event: dict[str, Any]) -> dict[str, Any]:
    sm = event.get("source_metrics") if isinstance(event.get("source_metrics"), dict) else {}
    return {
        "source_updated_at": event.get("source_updated_at"),
        "event_start": event.get("event_start") or sm.get("wildfire_start_date"),
        "event_end": event.get("event_end"),
        "last_detection": event.get("last_detection") or sm.get("wildfire_last_detection"),
        "operational_event_date": event.get("event_date"),
    }


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = load_json(LATEST, {})
    lifecycle = load_json(LIFECYCLE, {})
    generated = str(snap.get("generated_at") or "")
    try:
        run_dt = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        run_dt = now_utc()
        generated = iso(run_dt)
    day_path = DAILY_ROOT / run_dt.strftime("%Y") / run_dt.strftime("%m") / f"{run_dt.strftime('%d')}.json"
    day_path.parent.mkdir(parents=True, exist_ok=True)
    day = load_json(day_path, {
        "schema_version": "2.0",
        "date_utc": run_dt.date().isoformat(),
        "first_run_at": generated,
        "last_run_at": generated,
        "run_count": 0,
        "source_runs": [],
        "raw_archive_authority": "data/events/archive (3x-daily enriched snapshots)",
        "events": {},
    })
    day["schema_version"] = "2.0"
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
        life = lifecycle_snapshot(event, lifecycle)
        sig_now = public_significant(event)
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
            "temporal_latest": temporal_fields(event),
            "lifecycle_first_seen": min_iso(prev.get("lifecycle_first_seen"), life.get("first_seen")) or prev.get("first_observed_at") or generated,
            "lifecycle_last_seen": max_iso(prev.get("lifecycle_last_seen"), life.get("last_seen"), generated) or generated,
            "lifecycle_source_observations": max(int(prev.get("lifecycle_source_observations", 0) or 0), int(life.get("source_observations", 0) or 0)),
            "alert_level_latest": level,
            "max_alert_level": prev.get("max_alert_level") or level,
            "public_significant_latest": sig_now,
            "public_significant": bool(prev.get("public_significant")) or sig_now,
            "display_rule_latest": event.get("display_rule"),
            "raw_metrics_latest": current_metrics,
            "raw_metrics_max": merge_max(prev.get("raw_metrics_max") or {}, current_metrics),
            "metric_provenance": deepcopy((event.get("exposure") or {}).get("metric_provenance") or {}),
        }
        old_level = str(prev.get("max_alert_level") or "")
        if ALERT_RANK.get(level, 0) >= ALERT_RANK.get(old_level, 0):
            rec["max_alert_level"] = level or old_level
        geom = reporting_geometry(event)
        if geom and "geometry" in geom:
            geom["captured_at"] = generated
            rec["reporting_footprint"] = geom
        elif prev.get("reporting_footprint"):
            rec["reporting_footprint"] = prev["reporting_footprint"]
        elif geom:
            rec["reporting_footprint_status"] = geom
        events_table[stable] = rec

    day["event_count"] = len(events_table)
    day["public_significant_event_count"] = sum(1 for e in events_table.values() if e.get("public_significant"))
    day_path.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "daily_ledger": str(day_path.relative_to(ROOT)),
        "date_utc": day["date_utc"],
        "run_count": day["run_count"],
        "event_count": day["event_count"],
        "public_significant_event_count": day["public_significant_event_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
