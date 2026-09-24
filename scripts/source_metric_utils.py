#!/usr/bin/env python3
"""Pure parsing helpers for Climate Pulse's source-first hazard metrics.

The helpers in this module intentionally do not perform network requests. They
normalize structured GDACS/GWIS payloads so source values can be preferred over
Climate Pulse fallback estimates.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable


def walk_dicts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_dicts(value)


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        x = float(value)
        return int(round(x)) if math.isfinite(x) and x >= 0 else None
    text = str(value).strip().replace("\u00a0", " ")
    if not text or text.lower() in {"n/a", "na", "null", "none", "-"}:
        return None
    text = re.sub(r"(?<=\d)[,\s](?=\d{3}(?:\D|$))", "", text)
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        x = float(m.group(0))
    except ValueError:
        return None
    return int(round(x)) if math.isfinite(x) and x >= 0 else None


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        return x if math.isfinite(x) else None
    text = str(value).strip().replace("\u00a0", " ")
    text = re.sub(r"(?<=\d)[,\s](?=\d{3}(?:\D|$))", "", text)
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        x = float(m.group(0))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def normalized_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper())


def latest_episode_id(event_payload: Any) -> int | None:
    found: list[int] = []
    for d in walk_dicts(event_payload):
        for key in ("episodeid", "episode_id", "episode"):
            if key in d:
                try:
                    found.append(int(d[key]))
                except (TypeError, ValueError):
                    pass
    return max(found) if found else None


def episode_details_url(event_payload: Any, episode_id: int | None = None) -> str | None:
    candidates: list[tuple[int, str]] = []
    for d in walk_dicts(event_payload):
        url = d.get("details")
        if not isinstance(url, str) or "getepisodedata" not in url.lower():
            continue
        eid = None
        for key in ("episodeid", "episode_id", "episode"):
            if key in d:
                try:
                    eid = int(d[key])
                except (TypeError, ValueError):
                    pass
                break
        if episode_id is not None and eid == episode_id:
            return url
        candidates.append((eid if eid is not None else -1, url))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def impact_resources(episode_payload: Any, resource_key: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in walk_dicts(episode_payload):
        impacts = d.get("impacts")
        if not isinstance(impacts, list):
            continue
        for item in impacts:
            if not isinstance(item, dict):
                continue
            resource = item.get("resource")
            if not isinstance(resource, dict):
                continue
            url = resource.get(resource_key)
            if not isinstance(url, str) or not url.strip() or url in seen:
                continue
            seen.add(url)
            out.append((str(item.get("source") or "GDACS"), url.strip()))
    return out


def _find_named_scalar(obj: Any, wanted: set[str]) -> int | None:
    for d in walk_dicts(obj):
        for k, v in d.items():
            if normalized_key(k) in wanted:
                n = as_int(v)
                if n is not None:
                    return n
        name = None
        for key in ("name", "key", "scalar", "scalarname", "field", "metric"):
            if key in d:
                name = normalized_key(d.get(key))
                break
        if name in wanted:
            for key in ("value", "val", "amount", "count", "number"):
                if key in d:
                    n = as_int(d.get(key))
                    if n is not None:
                        return n
    return None


def parse_wildfire_impact(payload: Any) -> dict[str, Any]:
    pop_scopes: list[Any] = []
    for d in walk_dicts(payload):
        datasource = normalized_key(d.get("datasource") or d.get("data_source"))
        if datasource == "POP":
            pop_scopes.append(d)
    scopes = pop_scopes or [payload]
    aliases = {
        "population_burned_area": {"POPAFFECTED", "SUMPOP0.0", "SUMPOP0"},
        "population_within_1km": {"SUMPOP1.0", "SUMPOP1"},
        "population_within_2km": {"SUMPOP2.0", "SUMPOP2"},
        "population_within_5km": {"SUMPOP5.0", "SUMPOP5"},
        "population_within_10km": {"SUMPOP10.0", "SUMPOP10"},
    }
    out: dict[str, Any] = {}
    for field, wanted in aliases.items():
        for scope in scopes:
            value = _find_named_scalar(scope, wanted)
            if value is not None:
                out[field] = value
                break
    for d in walk_dicts(payload):
        for key in ("modelrun", "modelstatus", "modelname"):
            if key in d and d.get(key) not in (None, ""):
                out.setdefault({"modelrun":"model_run","modelstatus":"model_status","modelname":"model_name"}[key], str(d.get(key)))
    return out


def _timeline_items(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for d in walk_dicts(payload):
        items = d.get("item")
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or id(item) in seen:
                continue
            if any(k in item for k in ("pop39", "pop74", "popstormsurge")):
                seen.add(id(item)); out.append(item)
    if not out:
        for d in walk_dicts(payload):
            if any(k in d for k in ("pop39", "pop74", "popstormsurge")):
                out.append(d)
    return out


def _advisory_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    for fmt in ("%d %b %Y %H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_cyclone_timeline(payload: Any) -> dict[str, Any]:
    items = _timeline_items(payload)
    if not items:
        return {}
    def rank(item: dict[str, Any]):
        return (1 if truthy(item.get("current")) and truthy(item.get("actual")) else 0,
                1 if truthy(item.get("actual")) else 0,
                _advisory_dt(item.get("advisory_datetime")))
    item = max(items, key=rank)
    out: dict[str, Any] = {}
    for src, dst in (("pop39","population_wind_39kt"),("pop74","population_wind_74kt"),("popstormsurge","population_storm_surge")):
        n = as_int(item.get(src))
        if n is not None:
            out[dst] = n
    for key in ("id", "advisory_number", "advisory_datetime", "wind_speed", "coordinates", "alertscore"):
        if item.get(key) not in (None, ""):
            out[key] = item.get(key)
    out["actual"] = truthy(item.get("actual")); out["current"] = truthy(item.get("current"))
    return out


def gdacs_alert_level(event: dict[str, Any]) -> str | None:
    explicit = event.get("alert_level")
    if explicit:
        s = str(explicit).strip().title()
        if s in {"Green", "Orange", "Red"}:
            return s
    text = " ".join(str(x or "") for x in (event.get("source"), event.get("summary"))).lower()
    for level in ("red", "orange", "green"):
        if re.search(rf"\b{level}\b", text):
            return level.title()
    return None


def drought_agricultural_impact_area(summary: Any) -> float | None:
    text = str(summary or ""); low = text.lower()
    if "drought" not in low or not any(k in low for k in ("impact", "risk")):
        return None
    m = re.search(r"([0-9][0-9,\s]*(?:\.\d+)?)\s*km\s*(?:²|2|\^2)?", text, re.I)
    return as_float(m.group(1)) if m else None
