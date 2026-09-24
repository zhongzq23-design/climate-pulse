#!/usr/bin/env python3
"""Run climate-context enrichment against the full public event universe.

Why this wrapper exists
-----------------------
The browser publishes ``canonical_events`` while some operational enrichment
steps historically used ``events``. A new event can therefore appear publicly
before it is present in the operational array. Climate context must never be
computed from only one of those views.

This runner builds the de-duplicated union of ``canonical_events`` and
``events`` (canonical first), temporarily exposes that union to the existing
climate enrichment implementation, then copies the resulting references back
to both snapshot views. Publication fails closed if any public canonical event
is left without either a ready context or an explicit unavailable reason.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from enrich_climate_context import main as enrich_main

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def valid_coord(event: dict[str, Any]) -> bool:
    try:
        lat = float(event.get("lat"))
        lon = float(event.get("lon"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180


def merged_public_universe(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical ∪ events, de-duplicated by event ID.

    Canonical records are preferred because that is the browser-facing view.
    Missing fields are filled from the operational event record when available.
    """
    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for key in ("canonical_events", "events"):
        items = snapshot.get(key)
        if not isinstance(items, list):
            continue
        for raw in items:
            if not isinstance(raw, dict):
                continue
            event_id = str(raw.get("id") or "").strip()
            if not event_id:
                raise RuntimeError(f"{key} contains an event without a stable id")

            if event_id not in by_id:
                item = copy.deepcopy(raw)
                by_id[event_id] = item
                ordered.append(item)
            else:
                item = by_id[event_id]
                for field, value in raw.items():
                    if field not in item or item[field] in (None, "", [], {}):
                        item[field] = copy.deepcopy(value)

    for event in ordered:
        if not valid_coord(event):
            event["climate_context"] = {
                "status": "unavailable",
                "reason": "Reported coordinates are missing or invalid; climate context cannot be sampled.",
            }

    return ordered


def validate_ref(event_id: str, ref: Any) -> None:
    if not isinstance(ref, dict):
        raise RuntimeError(f"Climate coverage gap for {event_id}: no climate_context object")
    status = ref.get("status")
    if status == "ready":
        path = str(ref.get("path") or "").strip()
        if not path:
            raise RuntimeError(f"Climate coverage gap for {event_id}: ready without path")
        if not (ROOT / path).exists():
            raise RuntimeError(f"Climate coverage gap for {event_id}: referenced file is missing: {path}")
        return
    if status == "unavailable":
        if not str(ref.get("reason") or "").strip():
            raise RuntimeError(f"Climate coverage gap for {event_id}: unavailable without reason")
        return
    raise RuntimeError(f"Climate coverage gap for {event_id}: invalid status {status!r}")


def apply_refs_and_assert(
    original: dict[str, Any], enriched: dict[str, Any]
) -> tuple[dict[str, Any], int, int]:
    enriched_events = enriched.get("events")
    if not isinstance(enriched_events, list):
        raise RuntimeError("Climate enrichment did not return an event list")

    refs: dict[str, dict[str, Any]] = {}
    for event in enriched_events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        ref = event.get("climate_context")
        validate_ref(event_id, ref)
        refs[event_id] = copy.deepcopy(ref)

    final = copy.deepcopy(original)
    final["schema_version"] = enriched.get("schema_version", final.get("schema_version"))
    climate_monitor = (enriched.get("monitor") or {}).get("climate_context")
    if climate_monitor is not None:
        final.setdefault("monitor", {})["climate_context"] = copy.deepcopy(climate_monitor)

    matched = 0
    canonical_count = 0
    for key in ("events", "canonical_events"):
        items = final.get(key)
        if not isinstance(items, list):
            continue
        for event in items:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                raise RuntimeError(f"{key} contains an event without a stable id")
            ref = refs.get(event_id)
            if ref is None:
                raise RuntimeError(
                    f"Climate coverage gap: published {key} event {event_id} was not processed"
                )
            event["climate_context"] = copy.deepcopy(ref)
            matched += 1
            if key == "canonical_events":
                validate_ref(event_id, ref)
                canonical_count += 1

    return final, matched, canonical_count


def main() -> None:
    original = load(LATEST, {})
    if not isinstance(original, dict):
        raise RuntimeError("latest.json is not a JSON object")

    universe = merged_public_universe(original)
    if not universe:
        raise RuntimeError("No public events are available for climate enrichment")

    working = copy.deepcopy(original)
    working["events"] = universe
    LATEST.write_text(json.dumps(working, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    enrich_main()
    enriched = load(LATEST, {})
    if not isinstance(enriched, dict):
        raise RuntimeError("Climate enrichment produced an invalid latest.json")

    final, matched, canonical_count = apply_refs_and_assert(original, enriched)
    LATEST.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "public_union_count": len(universe),
        "matched_event_views": matched,
        "canonical_events_covered": canonical_count,
        "coverage": "complete",
    }, indent=2))


if __name__ == "__main__":
    main()
