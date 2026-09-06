#!/usr/bin/env python3
"""Recover recently active GDACS wildfires whose start date falls outside the 7-day discovery window.

GDACS SEARCH can key filtering to event start date. A wildfire that started more
than seven days ago but still had detections in the last seven days can therefore
fall out of the source list even though it remains operationally recent. This
helper queries two older start-date bands, keeps only records whose GDACS `todate`
(or equivalent parsed event date) is still within the normal 7-day freshness
window, and merges them into the current snapshot before source-first enrichment.
"""
from __future__ import annotations

import json
import urllib.parse
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from monitor_events import (
    GDACS_URL, LATEST_PATH, cluster_fires, dedupe, fetch_json, parse_dt, parse_gdacs,
    utcnow,
)

ROOT = Path(__file__).resolve().parents[1]
FRESH_DAYS = 7
OLDER_BANDS = ((8, 14), (15, 21))


def recent_by_last_detection(event: dict[str, Any], now) -> bool:
    dt = parse_dt(event.get("event_date"))
    if dt is None:
        return False
    return dt >= now - timedelta(days=FRESH_DAYS)


def fetch_band(now, old_min: int, old_max: int) -> list[dict[str, Any]]:
    # `fromDate`/`toDate` are intentionally a historical start-date band.
    start = (now - timedelta(days=old_max)).date().isoformat()
    end = (now - timedelta(days=old_min)).date().isoformat()
    url = GDACS_URL + "?" + urllib.parse.urlencode({
        "fromDate": start,
        "toDate": end,
        "alertlevel": "Green;Orange;Red",
        "eventlist": "WF",
    })
    diag = {
        "wildfire_raw": 0,
        "wildfire_with_area": 0,
        "wildfire_major": 0,
        "wildfire_below_threshold": 0,
        "wildfire_area_unknown": 0,
    }
    rows = parse_gdacs(fetch_json(url), now, diag)
    return [e for e in rows if e.get("type") == "Wildfire" and recent_by_last_detection(e, now)]


def merge_unique(existing: list[dict[str, Any]], extras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [deepcopy(x) for x in existing]
    seen = {str(x.get("id")) for x in out}
    for e in extras:
        key = str(e.get("id"))
        if key in seen:
            continue
        out.append(deepcopy(e))
        seen.add(key)
    return out


def main() -> None:
    if not LATEST_PATH.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    now = utcnow()
    recovered: list[dict[str, Any]] = []
    errors: list[str] = []
    for old_min, old_max in OLDER_BANDS:
        try:
            recovered.extend(fetch_band(now, old_min, old_max))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{old_min}-{old_max}d:{type(exc).__name__}:{str(exc)[:140]}")

    # Deduplicate recovered source IDs, then merge them with the standard 7-day feed.
    recovered_by_id = {str(e.get("id")): e for e in recovered}
    source_events = merge_unique(snap.get("source_events") or [], list(recovered_by_id.values()))
    canonical = dedupe(source_events)
    snap["source_events"] = source_events
    snap["canonical_events"] = canonical
    snap["events"] = cluster_fires(canonical)
    snap.setdefault("monitor", {})["wildfire_discovery_extension"] = {
        "purpose": "recover wildfires started 8-21 days ago when last detection remains within the standard 7-day freshness window",
        "freshness_days": FRESH_DAYS,
        "start_date_bands_days_ago": [list(x) for x in OLDER_BANDS],
        "recovered_source_events": len(recovered_by_id),
        "errors": errors,
    }
    LATEST_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok" if not errors else "completed_with_warnings",
        "recovered_source_events": len(recovered_by_id),
        "source_events": len(source_events),
        "canonical_events": len(canonical),
        "errors": errors,
    }, indent=2))


if __name__ == "__main__":
    main()
