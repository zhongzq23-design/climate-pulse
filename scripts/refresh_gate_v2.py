#!/usr/bin/env python3
"""Semantic refresh gate for the scheduled Climate Pulse production refresh.

SOURCE_CHANGE_SEMANTIC_FINGERPRINT_V2 separates scientific/event changes from
feed-maintenance churn.  Expensive hazard products are refreshed only when a
source event changes in a way that can affect event identity, location, event
extent/timing, severity, or source-aligned wildfire metrics.

Fields such as ``source_updated_at`` and the derived ``event_date`` often move
when GDACS republishes an otherwise unchanged record.  Those fields remain in
the event snapshot for provenance/display, but they do not by themselves force
population, footprint, or asset recomputation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IFS_COLLECTION = "ECMWF/NRT_FORECAST/IFS/OPER"

# Used only to quantify non-scientific feed churn for diagnostics.  This is
# intentionally broader than the semantic projection below.
CONTENT_VOLATILE_KEYS = {
    "generated_at",
    "calculated_at",
    "checked_at",
    "fetched_at",
    "retrieved_at",
    "last_seen",
    "updated",  # relative-age presentation copy
    "observations",
    "climate_context",
    "exposure",
    "asset_exposure",
    "footprint",
    "footprint_qc",
    "source_metrics",
    "display_eligible",
    "display_rule",
}

# These normalized source fields are the contract for expensive event-product
# invalidation.  Deliberately excluded: title/region/source/source_url,
# source_updated_at and event_date.  The latter two frequently mirror feed
# republication time rather than a physical event change.
SEMANTIC_FIELDS = (
    "id",
    "source_id",
    "origin",
    "type",
    "lat",
    "lon",
    "status",
    "priority",
    "event_start",
    "event_end",
    "last_detection",
    "burned_area_ha",
    "gdacs_id",
    "summary",  # carries source severity/wind/magnitude information
)
TIME_FIELDS = {"event_start", "event_end", "last_detection"}
FLOAT_FIELDS = {"lat", "lon", "burned_area_ha"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalized_time(value: Any) -> Any:
    dt = parse_iso(value)
    if dt is None:
        text = str(value or "").strip()
        return text or None
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_text(value: Any) -> Any:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _normalized_float(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(number):
        return None
    # Preserve physically meaningful center movement while suppressing
    # serialization noise. Five decimal degrees is roughly metre scale.
    if field in {"lat", "lon"}:
        return round(number, 5)
    # Sub-hectare wildfire changes are irrelevant to the 10,000 ha admission
    # threshold and downstream exposure geometry.
    if field == "burned_area_ha":
        return round(number, 1)
    return round(number, 10)


def semantic_source_event(row: dict[str, Any]) -> dict[str, Any]:
    """Return the source fields that can invalidate expensive products."""
    out: dict[str, Any] = {}
    for field in SEMANTIC_FIELDS:
        value = row.get(field)
        if field in TIME_FIELDS:
            value = _normalized_time(value)
        elif field in FLOAT_FIELDS:
            value = _normalized_float(field, value)
        elif field == "summary":
            value = _normalized_text(value)
        elif isinstance(value, str):
            value = value.strip() or None
        out[field] = value
    return out


def _content_stable(value: Any) -> Any:
    """Normalize broad source content for churn diagnostics only."""
    if isinstance(value, dict):
        return {
            str(k): _content_stable(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
            if str(k) not in CONTENT_VOLATILE_KEYS
        }
    if isinstance(value, list):
        rows = [_content_stable(v) for v in value]
        if all(isinstance(v, dict) for v in rows):
            rows.sort(
                key=lambda v: (
                    str(v.get("id") or ""),
                    str(v.get("origin") or ""),
                    str(v.get("source_id") or ""),
                    str(v.get("type") or ""),
                )
            )
        return rows
    if isinstance(value, float):
        return round(value, 10)
    return value


def _source_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get("source_events")
    if not isinstance(rows, list):
        rows = snapshot.get("canonical_events") or snapshot.get("events") or []
    return [row for row in rows if isinstance(row, dict)]


def source_key(row: dict[str, Any]) -> str:
    key = str(row.get("id") or "").strip()
    if key:
        return key
    return "|".join(str(row.get(k) or "") for k in ("origin", "source_id", "type"))


def event_payload(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [semantic_source_event(row) for row in _source_rows(snapshot)]
    rows.sort(
        key=lambda row: (
            str(row.get("id") or ""),
            str(row.get("origin") or ""),
            str(row.get("source_id") or ""),
            str(row.get("type") or ""),
        )
    )
    return rows


def event_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        event_payload(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def content_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        _content_stable(_source_rows(snapshot)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_event_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in _source_rows(snapshot):
        key = source_key(row)
        if key:
            out[key] = semantic_source_event(row)
    return out


def content_source_event_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in _source_rows(snapshot):
        key = source_key(row)
        if key:
            out[key] = _content_stable(row)
    return out


def _changed_ids(old: dict[str, Any], new: dict[str, Any]) -> tuple[set[str], set[str]]:
    changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    removed = set(old) - set(new)
    return changed, removed


def changed_source_ids(previous: dict[str, Any], current: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return only semantically meaningful source changes."""
    return _changed_ids(source_event_map(previous), source_event_map(current))


def changed_content_source_ids(previous: dict[str, Any], current: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return broader normalized feed changes for observability, not invalidation."""
    return _changed_ids(content_source_event_map(previous), content_source_event_map(current))


def event_source_keys(event: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    event_id = str(event.get("id") or "").strip()
    if event_id:
        keys.add(event_id)
    origin = str(event.get("origin") or "").strip()
    source_id = str(event.get("source_id") or "").strip()
    if origin and source_id:
        keys.add(f"{origin}|{source_id}|{event.get('type') or ''}")
    for field in ("source_members", "members"):
        values = event.get(field)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    keys.update(event_source_keys(value))
    return keys


def canonical_source_index(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for event in snapshot.get("canonical_events") or []:
        if not isinstance(event, dict) or event.get("id") is None:
            continue
        canonical_id = str(event.get("id"))
        for key in event_source_keys(event):
            index.setdefault(key, set()).add(canonical_id)
    return index


def incremental_scope(
    previous: dict[str, Any], current: dict[str, Any], force: bool
) -> dict[str, Any]:
    changed_raw, removed_raw = changed_source_ids(previous, current)
    if force or not changed_raw:
        return {
            "incremental_refresh": False,
            "changed_event_ids": [],
            "changed_source_ids": sorted(changed_raw),
            "removed_source_ids": sorted(removed_raw),
            "unresolved_current_source_ids": [],
            "threshold": 0,
        }

    current_raw = source_event_map(current)
    current_index = canonical_source_index(current)
    previous_index = canonical_source_index(previous)
    current_canonical_ids = {
        str(e.get("id"))
        for e in current.get("canonical_events") or []
        if isinstance(e, dict) and e.get("id") is not None
    }

    changed_canonical: set[str] = set()
    unresolved_current: set[str] = set()
    for raw_id in changed_raw:
        mapped = set(current_index.get(raw_id, set()))
        mapped.update(previous_index.get(raw_id, set()) & current_canonical_ids)
        if raw_id in current_canonical_ids:
            mapped.add(raw_id)
        if raw_id in current_raw and not mapped:
            unresolved_current.add(raw_id)
        changed_canonical.update(mapped)

    n = len(current_canonical_ids)
    threshold = max(12, int(math.ceil(n * 0.25))) if n else 0
    safe = not unresolved_current and len(changed_canonical) <= threshold
    return {
        "incremental_refresh": bool(safe),
        "changed_event_ids": sorted(changed_canonical),
        "changed_source_ids": sorted(changed_raw),
        "removed_source_ids": sorted(removed_raw),
        "unresolved_current_source_ids": sorted(unresolved_current),
        "threshold": threshold,
    }


def previous_cycle_iso(index: dict[str, Any]) -> str | None:
    recent = index.get("recent_window") if isinstance(index, dict) else None
    if not isinstance(recent, dict):
        return None
    cycles = recent.get("daily_cycle_times")
    if isinstance(cycles, list) and cycles:
        return str(cycles[-1])
    return None


def iso_from_ms(value: int | float) -> str:
    return (
        datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def init_earth_engine() -> Any:
    import ee

    project = (os.environ.get("EE_PROJECT") or os.environ.get("GEE_PROJECT_ID") or "").strip() or None
    key_json = (
        os.environ.get("EE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GEE_SERVICE_ACCOUNT_JSON")
        or ""
    ).strip()
    key_file = (os.environ.get("EE_SERVICE_ACCOUNT_KEY_FILE") or "").strip() or None
    temp_name: str | None = None
    try:
        if key_json:
            parsed = json.loads(key_json)
            project = project or parsed.get("project_id")
            handle = tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            )
            json.dump(parsed, handle)
            handle.close()
            temp_name = handle.name
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


def latest_complete_ifs_cycle() -> str:
    ee = init_earth_engine()
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    floor_ms = int((now - timedelta(days=10)).timestamp() * 1000)
    image = (
        ee.ImageCollection(IFS_COLLECTION)
        .filter(ee.Filter.eq("model", "ifs"))
        .filter(ee.Filter.eq("forecast_hours", 24))
        .filter(ee.Filter.lte("forecast_time", now_ms))
        .filter(ee.Filter.gte("creation_time", floor_ms))
        .sort("creation_time", False)
        .first()
    )
    cycle_ms = image.get("creation_time").getInfo()
    if cycle_ms is None:
        raise RuntimeError("No fully elapsed ECMWF IFS 0-24 h cycle is available")
    return iso_from_ms(cycle_ms)


def emit(name: str, value: Any) -> None:
    text = str(value).lower() if isinstance(value, bool) else str(value)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--climate-index", required=True)
    parser.add_argument("--github-event", default="schedule")
    args = parser.parse_args()

    previous = load_json(Path(args.previous))
    current = load_json(Path(args.current))
    climate_index = load_json(Path(args.climate_index))
    previous = previous if isinstance(previous, dict) else {}
    current = current if isinstance(current, dict) else {}

    previous_fp = event_fingerprint(previous)
    current_fp = event_fingerprint(current)
    event_changed = previous_fp != current_fp

    previous_content_fp = content_fingerprint(previous)
    current_content_fp = content_fingerprint(current)
    broad_content_changed = previous_content_fp != current_content_fp
    broad_changed_ids, _ = changed_content_source_ids(previous, current)
    semantic_changed_ids, _ = changed_source_ids(previous, current)
    suppressed_churn_ids = broad_changed_ids - semantic_changed_ids

    previous_time = parse_iso(previous.get("generated_at"))
    current_time = parse_iso(current.get("generated_at"))
    daily_due = bool(
        current_time is not None
        and (previous_time is None or current_time.date() != previous_time.date())
    )

    force = str(args.github_event) != "schedule"
    full_refresh = bool(force or event_changed)
    scope = incremental_scope(previous, current, force) if full_refresh else {
        "incremental_refresh": False,
        "changed_event_ids": [],
        "changed_source_ids": [],
        "removed_source_ids": [],
        "unresolved_current_source_ids": [],
        "threshold": 0,
    }

    prior_cycle = previous_cycle_iso(climate_index if isinstance(climate_index, dict) else {})
    latest_cycle = None
    ifs_probe = "not_needed"
    ifs_changed = False

    if not full_refresh:
        try:
            latest_cycle = latest_complete_ifs_cycle()
            ifs_probe = "ok"
            ifs_changed = prior_cycle is None or latest_cycle != prior_cycle
        except Exception as exc:  # noqa: BLE001
            ifs_probe = f"error:{type(exc).__name__}"
            ifs_changed = True
            print(f"WARN refresh gate IFS probe failed: {type(exc).__name__}: {exc}")

    climate_refresh = bool(full_refresh or ifs_changed)
    maintenance_refresh = bool(force or daily_due)
    any_refresh = bool(full_refresh or climate_refresh or maintenance_refresh)

    reasons: list[str] = []
    if force:
        reasons.append("manual_dispatch")
    if event_changed:
        reasons.append("source_semantics_changed")
    elif broad_content_changed:
        reasons.append("source_metadata_churn_suppressed")
    if daily_due:
        reasons.append("new_utc_day")
    if ifs_changed:
        reasons.append("ifs_cycle_changed_or_unverified")
    if not reasons:
        reasons.append("no_scientific_input_change")

    outputs = (
        ("refresh_needed", any_refresh),
        ("any_refresh", any_refresh),
        ("full_refresh", full_refresh),
        ("incremental_refresh", bool(scope["incremental_refresh"])),
        ("changed_event_ids", ",".join(scope["changed_event_ids"])),
        ("changed_event_count", len(scope["changed_event_ids"])),
        ("changed_source_count", len(scope["changed_source_ids"])),
        ("removed_source_count", len(scope["removed_source_ids"])),
        ("unresolved_source_count", len(scope["unresolved_current_source_ids"])),
        ("suppressed_source_churn_count", len(suppressed_churn_ids)),
        ("climate_refresh", climate_refresh),
        ("maintenance_refresh", maintenance_refresh),
        ("event_changed", event_changed),
        ("daily_due", daily_due),
        ("ifs_changed", ifs_changed),
        ("reason", ",".join(reasons)),
    )
    for name, value in outputs:
        emit(name, value)

    print(
        json.dumps(
            {
                "fingerprint_version": "SOURCE_CHANGE_SEMANTIC_FINGERPRINT_V2",
                "any_refresh": any_refresh,
                "full_refresh": full_refresh,
                "incremental_refresh": bool(scope["incremental_refresh"]),
                "changed_event_count": len(scope["changed_event_ids"]),
                "changed_event_ids": scope["changed_event_ids"],
                "changed_source_count": len(scope["changed_source_ids"]),
                "removed_source_count": len(scope["removed_source_ids"]),
                "broad_source_change_count": len(broad_changed_ids),
                "suppressed_source_churn_count": len(suppressed_churn_ids),
                "unresolved_current_source_ids": scope["unresolved_current_source_ids"],
                "incremental_threshold": scope["threshold"],
                "climate_refresh": climate_refresh,
                "maintenance_refresh": maintenance_refresh,
                "reason": reasons,
                "event_changed": event_changed,
                "broad_content_changed": broad_content_changed,
                "daily_due": daily_due,
                "ifs_probe": ifs_probe,
                "previous_ifs_cycle": prior_cycle,
                "latest_ifs_cycle": latest_cycle,
                "previous_event_fingerprint": previous_fp[:16],
                "current_event_fingerprint": current_fp[:16],
                "previous_content_fingerprint": previous_content_fp[:16],
                "current_content_fingerprint": current_content_fp[:16],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
