#!/usr/bin/env python3
"""Synchronize wildfire headline metrics to one exact GDACS/GWIS episode.

The event-list feed is discovery metadata. This step aligns public wildfire
burned area, last detection and duration to the exact GDACS episode already
used for source-native population exposure, then rebuilds the display list.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from copy import deepcopy
from pathlib import Path
from typing import Any

import enrich_hazard_exposure as h
import run_hazard_enrichment as sf
from monitor_events import cluster_fires
from source_metric_utils import episode_details_url, latest_episode_id
from wildfire_episode_metrics import parse_wildfire_episode_metrics

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
LIFECYCLE = ROOT / "data" / "events" / "lifecycle.json"
GDACS_EPISODE = "https://www.gdacs.org/gdacsapi/api/events/getepisodedata"
GREEN_WILDFIRE_MIN_HA = 10_000.0
GREEN_WILDFIRE_MIN_POP_5KM = 10_000


def _fetch_exact_episode(event_doc: Any, event_id: str, episode_id: int) -> Any | None:
    details = episode_details_url(event_doc, episode_id)
    if sf.gdacs_url(details):
        try:
            return h.fetch_json(details, timeout=45)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN exact episode URL WF {event_id}/{episode_id}: {type(exc).__name__}: {exc}")
    for source in ("OverAll", "GDACS", ""):
        params = {"eventtype": "WF", "eventid": event_id, "episodeid": episode_id}
        if source:
            params["source"] = source
        try:
            return h.fetch_json(GDACS_EPISODE + "?" + urllib.parse.urlencode(params), timeout=45)
        except Exception:
            pass  # noqa: BLE001
    return None


def _metric_provenance(episode_id: int, field: str) -> dict[str, Any]:
    return {
        "source": "GDACS/GWIS",
        "source_field": field,
        "method": "source_reported_current_episode",
        "derived_by_climate_pulse": False,
        "gdacs_episode_id": episode_id,
    }


def _replace_wildfire_area_in_summary(summary: Any, area_ha: float) -> str:
    text = str(summary or "")
    value = f"{int(round(area_ha)):,}"
    return re.sub(
        r"\b[0-9][0-9,.\s]*\s*(?:ha|hectares?)\b",
        f"{value} ha",
        text,
        flags=re.I,
    )


def _sync_event(original: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    event = deepcopy(original)
    if event.get("type") != "Wildfire":
        return event, False

    x = deepcopy(event.get("exposure")) if isinstance(event.get("exposure"), dict) else {}
    ids = h.gdacs_member_ids(event, "WF")
    if not ids:
        return event, False
    event_id = str(x.get("gdacs_event_id") or ids[0])

    try:
        event_doc = h.gdacs_detail("WF", event_id)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN wildfire event detail WF {event_id}: {type(exc).__name__}: {exc}")
        return event, False

    latest = latest_episode_id(event_doc)
    try:
        exposure_episode = int(x.get("gdacs_episode_id")) if x.get("gdacs_episode_id") is not None else None
    except (TypeError, ValueError):
        exposure_episode = None
    episode_id = exposure_episode if exposure_episode is not None else latest
    if episode_id is None:
        return event, False

    episode_doc = _fetch_exact_episode(event_doc, event_id, episode_id)
    metrics = parse_wildfire_episode_metrics(event_doc, episode_id)
    if episode_doc is not None:
        metrics.update(parse_wildfire_episode_metrics(episode_doc, episode_id))

    source_metrics = deepcopy(event.get("source_metrics")) if isinstance(event.get("source_metrics"), dict) else {}
    source_metrics["wildfire_current_episode_id"] = episode_id
    source_metrics["wildfire_episode_alignment"] = (
        "Headline burned area and population exposure are aligned to the same GDACS episode."
        if exposure_episode == episode_id else
        "No source-native population episode was available; wildfire headline metrics use the latest GDACS episode."
    )
    if latest is not None and latest > episode_id:
        source_metrics["wildfire_newer_episode_available"] = latest

    changed = False
    area = metrics.get("burned_area_ha")
    if area is not None:
        area = float(area)
        event["burned_area_ha"] = area
        x["burned_area_ha"] = area
        prov = deepcopy(x.get("metric_provenance")) if isinstance(x.get("metric_provenance"), dict) else {}
        prov["burned_area_ha"] = _metric_provenance(
            episode_id, str(metrics.get("burned_area_source_field") or "Ha")
        )
        x["metric_provenance"] = prov
        source_metrics["wildfire_burned_area_ha"] = area
        event["summary"] = _replace_wildfire_area_in_summary(event.get("summary"), area)
        changed = True

    if metrics.get("start_date"):
        source_metrics["wildfire_start_date"] = metrics["start_date"]
    if metrics.get("last_detection"):
        source_metrics["wildfire_last_detection"] = metrics["last_detection"]
        event["event_date"] = metrics["last_detection"]
    if metrics.get("duration_days") is not None:
        source_metrics["wildfire_duration_days"] = int(metrics["duration_days"])

    event["source_metrics"] = source_metrics
    if x:
        event["exposure"] = x
        h.write_exposure(event, x)

    if event.get("priority") in {"Medium", "High"}:
        eligible, rule = True, "gdacs_orange_red"
    else:
        try:
            eligible = (
                float(event.get("burned_area_ha")) >= GREEN_WILDFIRE_MIN_HA
                and int(x.get("population_within_5km")) > GREEN_WILDFIRE_MIN_POP_5KM
            )
        except (TypeError, ValueError):
            eligible = False
        rule = "green_requires_current_episode_10000ha_and_population_within_5km_gt_10000"
    event["display_eligible"] = eligible
    event["display_rule"] = rule
    return event, changed


def _patch_lifecycle(events: list[dict[str, Any]]) -> None:
    try:
        state = json.loads(LIFECYCLE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    table = state.get("events")
    if not isinstance(table, dict):
        return
    changed = False
    for event in events:
        if event.get("type") != "Wildfire":
            continue
        rec = table.get(str(event.get("id")))
        if not isinstance(rec, dict):
            continue
        source_metrics = event.get("source_metrics") or {}
        if event.get("burned_area_ha") is not None:
            rec["latest_burned_area_ha"] = event["burned_area_ha"]
            changed = True
        if source_metrics.get("wildfire_current_episode_id") is not None:
            rec["latest_gdacs_episode_id"] = source_metrics["wildfire_current_episode_id"]
            changed = True
        if source_metrics.get("wildfire_last_detection"):
            rec["latest_event_date"] = source_metrics["wildfire_last_detection"]
            changed = True
    if changed:
        LIFECYCLE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    synced: list[dict[str, Any]] = []
    synced_count = 0
    for event in snap.get("canonical_events") or []:
        out, changed = _sync_event(event)
        synced.append(out)
        synced_count += int(changed)

    visible = [e for e in synced if e.get("display_eligible", True)]
    display = cluster_fires(visible)
    for event in display:
        h.enrich_cluster_exposure(event)

    snap["canonical_events"] = synced
    snap["events"] = display
    snap["schema_version"] = "1.6"
    snap.setdefault("monitor", {})["wildfire_metric_alignment"] = (
        "Discovery uses GDACS event-list data; public wildfire headline burned area, "
        "last detection, duration and source-native population are aligned to one GDACS/GWIS episode."
    )
    snap["monitor"]["wildfire_green_display_rule"] = (
        "current-episode burned_area_ha >= 10000 AND population_within_5km > 10000"
    )
    snap.setdefault("hazard_exposure", {})["wildfire_source_alignment"] = (
        "GDACS/GWIS current-episode headline metrics; event-list values are discovery metadata only."
    )

    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)
    _patch_lifecycle(synced)
    print(json.dumps({
        "status": "ok",
        "wildfires_synced": synced_count,
        "canonical_events": len(synced),
        "display_events": len(display),
    }, indent=2))


if __name__ == "__main__":
    main()
