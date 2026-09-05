#!/usr/bin/env python3
"""Persist compact browser-facing event footprints already used by enrichment.

This runs after wildfire/cyclone and drought exposure enrichment. It reuses the
GDACS event IDs and episode IDs already stored in each event's exposure metadata,
so it does not need an additional event-detail lookup.

Only events with a usable polygon are given a public footprint reference.
The geometry written to Git is a simplified display outline. Population exposure
continues to use the unsimplified geometry inside the enrichment calculation.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

# Load the compatibility runner first so GDACS polygon repair is active.
import run_hazard_enrichment  # noqa: F401
import enrich_hazard_exposure as h

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
OUT_DIR = ROOT / "data" / "footprints"


def safe_name(event_id: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(event_id or "event"))[:160]


def round_coords(value: Any, digits: int = 4) -> Any:
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(x), digits) for x in value]
        return [round_coords(x, digits) for x in value]
    return value


def display_geometry(geom):
    minx, miny, maxx, maxy = geom.bounds
    span = max(maxx - minx, maxy - miny)
    tolerance = min(0.05, max(0.001, span / 600.0))
    simplified = geom.simplify(tolerance, preserve_topology=True)
    if simplified.is_empty:
        simplified = geom
    return simplified, tolerance


def source_descriptor(event: dict[str, Any]) -> tuple[str, str, int | None, str] | None:
    exposure = event.get("exposure")
    if not isinstance(exposure, dict):
        return None
    hazard = exposure.get("hazard")
    eid = exposure.get("gdacs_event_id")
    episode = exposure.get("gdacs_episode_id")
    method = str(exposure.get("footprint_method") or "")
    if not eid:
        return None
    if hazard == "wildfire":
        return "WF", str(eid), int(episode) if episode is not None else None, method
    if hazard == "tropical_cyclone":
        return "TC", str(eid), int(episode) if episode is not None else None, method
    if hazard == "drought":
        return "DR", str(eid), int(episode) if episode is not None else None, method
    return None


def fetch_geometry(event_type: str, event_id: str, episode_id: int | None):
    feats = h.polygon_features(event_type, event_id, episode_id)
    if not feats:
        return None, 0
    if event_type == "TC":
        geom, _ = h.tc_wind_union(feats)
    else:
        geom = h.polygons_union(feats)
    return geom, len(feats)


def write_doc(
    event: dict[str, Any],
    event_type: str,
    event_id: str,
    episode_id: int | None,
    method: str,
    geom,
    feature_count: int,
) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    simplified, tolerance = display_geometry(geom)
    gj = mapping(simplified)
    gj["coordinates"] = round_coords(gj.get("coordinates"))

    file_name = safe_name(event.get("id")) + ".json"
    rel = f"data/footprints/{file_name}"
    doc = {
        "schema_version": "1.0",
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "source": "GDACS",
        "gdacs_event_type": event_type,
        "gdacs_event_id": event_id,
        "gdacs_episode_id": episode_id,
        "footprint_method": method or "GDACS event polygon",
        "reported_center": {"lat": event.get("lat"), "lon": event.get("lon")},
        "geometry": gj,
        "source_feature_count": feature_count,
        "display_simplification": {
            "applied": True,
            "tolerance_degrees": round(float(tolerance), 6),
            "purpose": "browser orientation only",
        },
        "scientific_note": (
            "This compact geometry is simplified for browser display. "
            "Exposure calculations use the unsimplified mapped footprint."
        ),
    }
    (OUT_DIR / file_name).write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ready",
        "path": rel,
        "source": "GDACS",
        "method": method or "GDACS event polygon",
    }


def enrich_list(events: list[dict[str, Any]], cache: dict[tuple, tuple]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    diag = {"eligible": 0, "ready": 0, "missing": 0}
    for original in events:
        event = deepcopy(original)
        event.pop("footprint", None)
        desc = source_descriptor(event)
        if desc is None:
            out.append(event)
            continue
        diag["eligible"] += 1
        event_type, event_id, episode_id, method = desc
        key = (event_type, event_id, episode_id, method)
        if key not in cache:
            try:
                cache[key] = fetch_geometry(event_type, event_id, episode_id)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"WARN footprint {event_type} {event_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                cache[key] = (None, 0)
        geom, feature_count = cache[key]
        if geom is None or geom.is_empty:
            diag["missing"] += 1
            out.append(event)
            continue
        event["footprint"] = write_doc(
            event,
            event_type,
            event_id,
            episode_id,
            method,
            geom,
            feature_count,
        )
        diag["ready"] += 1
        out.append(event)
    return out, diag


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    cache: dict[tuple, tuple] = {}

    canonical, d1 = enrich_list(snap.get("canonical_events") or [], cache)
    display, d2 = enrich_list(snap.get("events") or [], cache)

    snap["canonical_events"] = canonical
    snap["events"] = display
    snap.setdefault("monitor", {})["event_footprints"] = {
        "source": "GDACS polygons already associated with enriched events",
        "public_geometry": "topology-preserving simplified browser outline",
        "no_polygon_behavior": "do not display a footprint panel",
    }
    snap["footprint_diagnostics"] = {
        "canonical": d1,
        "display": d2,
        "unique_source_geometries": len(cache),
    }

    LATEST.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    h.rewrite_matching_archive(snap)
    print(
        json.dumps(
            {
                "status": "ok",
                "canonical": d1,
                "display": d2,
                "unique_source_geometries": len(cache),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
