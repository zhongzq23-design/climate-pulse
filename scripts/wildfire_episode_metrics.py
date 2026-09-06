#!/usr/bin/env python3
"""Pure parsers for same-episode GDACS/GWIS wildfire headline metrics."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from source_metric_utils import as_float, as_int, normalized_key, walk_dicts


def _episode_number(d: dict[str, Any]) -> int | None:
    for key in ("episodeid", "episode_id", "episode"):
        if key in d:
            try:
                return int(d[key])
            except (TypeError, ValueError):
                return None
    return None


def _episode_scopes(payload: Any, episode_id: int | None) -> list[Any]:
    if episode_id is None:
        return [payload]
    matches = [d for d in walk_dicts(payload) if _episode_number(d) == episode_id]
    return matches or [payload]


def _parse_area_text(value: Any) -> float | None:
    text = str(value or "")
    m = re.search(r"([0-9][0-9,.\s]*)\s*(?:ha|hectares?)\b", text, re.I)
    if not m:
        return None
    return as_float(m.group(1))


def _normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _first_named(scope: Any, wanted: set[str]) -> tuple[Any, str] | tuple[None, None]:
    for d in walk_dicts(scope):
        for key, value in d.items():
            if normalized_key(key) in wanted and value not in (None, ""):
                return value, str(key)
    return None, None


def parse_wildfire_episode_metrics(payload: Any, episode_id: int | None = None) -> dict[str, Any]:
    """Extract burned area, dates and duration for a requested GDACS episode."""
    scopes = _episode_scopes(payload, episode_id)
    out: dict[str, Any] = {}
    area_keys = {
        "HA", "HECTARES", "BURNEDAREA", "BURNEDAREAHA", "BURNEDAREAINHA",
        "AREAHA", "AREAINHA",
    }
    from_keys = {"FROMDATE", "STARTDATE", "EVENTSTARTDATE", "DATEFROM"}
    to_keys = {"TODATE", "LASTDETECTION", "LASTDETECTIONDATE", "ENDDATE", "DATETO"}
    duration_keys = {"DURATION", "DURATIONDAYS", "DAYS"}

    for scope in scopes:
        if "burned_area_ha" not in out:
            raw, field = _first_named(scope, area_keys)
            area = as_float(raw)
            if area is not None and area >= 0:
                out["burned_area_ha"] = area
                out["burned_area_source_field"] = field

        if "burned_area_ha" not in out:
            for d in walk_dicts(scope):
                for key in ("severitytext", "description", "title", "name"):
                    area = _parse_area_text(d.get(key))
                    if area is not None:
                        out["burned_area_ha"] = area
                        out["burned_area_source_field"] = key
                        break
                if "burned_area_ha" in out:
                    break

        if "start_date" not in out:
            raw, field = _first_named(scope, from_keys)
            value = _normalize_date(raw)
            if value:
                out["start_date"] = value
                out["start_date_source_field"] = field

        if "last_detection" not in out:
            raw, field = _first_named(scope, to_keys)
            value = _normalize_date(raw)
            if value:
                out["last_detection"] = value
                out["last_detection_source_field"] = field

        if "duration_days" not in out:
            raw, field = _first_named(scope, duration_keys)
            value = as_int(raw)
            if value is not None:
                out["duration_days"] = value
                out["duration_source_field"] = field

    if "duration_days" not in out and out.get("start_date") and out.get("last_detection"):
        try:
            out["duration_days"] = (
                date.fromisoformat(out["last_detection"]) - date.fromisoformat(out["start_date"])
            ).days
            out["duration_source_field"] = "derived_from_GDACS_fromdate_todate"
        except ValueError:
            pass
    if episode_id is not None:
        out["gdacs_episode_id"] = int(episode_id)
    return out
