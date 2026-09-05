#!/usr/bin/env python3
"""Persist compact browser-facing event footprints and transient analysis geometry.

The public geometry written under ``data/footprints`` is topology-preserving and
simplified for browser orientation. The unsimplified source geometry is written
only to ``.runtime/event_geometries`` for later exposure calculations in the
same GitHub Actions job and is never committed.

Wildfire and tropical-cyclone enrichment already carries GDACS event/episode IDs.
Drought and flood footprints can be resolved directly from their GDACS source IDs,
which means drought no longer needs a separate population-exposure pass merely to
make a footprint available.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

# Load the compatibility runner first so invalid GDACS polygons are repaired.
import run_hazard_enrichment  # noqa: F401
import enrich_hazard_exposure as h

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
OUT_DIR = ROOT / "data" / "footprints"
RUNTIME_DIR = ROOT / ".runtime" / "event_geometries"
TYPE_CODE = {"Wildfire": "WF", "Storm": "TC", "Drought": "DR", "Flood": "FL"}
DEFAULT_METHOD = {
    "WF": "GDACS burned-area/event polygon",
    "TC": "GDACS tropical-cyclone wind/impact polygon",
    "DR": "GDACS drought event polygon",
    "FL": "GDACS flood event polygon",
}


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


def source_descriptor(
    event: dict[str, Any],
    episode_cache: dict[tuple[str, str], int | None],
) -> tuple[str, str, int | None, str] | None:
    code = TYPE_CODE.get(str(event.get("type")))
    if code is None:
        return None

    exposure = event.get("exposure") if isinstance(event.get("exposure"), dict) else {}
    eid = exposure.get("gdacs_event_id")
    episode = exposure.get("gdacs_episode_id")
    method = str(exposure.get("footprint_method") or "")

    if not eid:
        ids = h.gdacs_member_ids(event, code)
        if not ids:
            return None
        eid = ids[0]
    eid = str(eid)

    if episode is None:
        key = (code, eid)
        if key not in episode_cache:
            episode_cache[key] = h.latest_episode(code, eid)
        episode = episode_cache[key]

    return code, eid, int(episode) if episode is not None else None, method or DEFAULT_METHOD[code]


def fetch_geometry(event_type: str, event_id: str, episode_id: int | None):
    feats = h.polygon_features(event_type, event_id, episode_id)
    if not feats:
        return None, 0, DEFAULT_METHOD.get(event_type, "GDACS event polygon")
    if event_type == "TC":
        geom, method = h.tc_wind_union(feats)
    else:
        geom = h.polygons_union(feats)
        method = DEFAULT_METHOD.get(event_type, "GDACS event polygon")
    return geom, len(feats), method


def write_runtime_geometry(
    event: dict[str, Any], event_type: str, event_id: str,
    episode_id: int | None, method: str, geom,
) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "gdacs_event_type": event_type,
        "gdacs_event_id": event_id,
        "gdacs_episode_id": episode_id,
        "footprint_method": method,
        "geometry": mapping(geom),
    }
    (RUNTIME_DIR / f"{safe_name(event.get('id'))}.json").write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_doc(
    event: dict[str, Any], event_type: str, event_id: str,
    episode_id: int | None, method: str, geom, feature_count: int,
) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    simplified, tolerance = display_geometry(geom)
    gj = mapping(simplified)
    gj["coordinates"] = round_coords(gj.get("coordinates"))

    file_name = safe_name(event.get("id")) + ".json"
    rel = f"data/footprints/{file_name}"
    doc = {
        "schema_version": "1.1",
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "source": "GDACS",
        "gdacs_event_type": event_type,
        "gdacs_event_id": event_id,
        "gdacs_episode_id": episode_id,
        "footprint_method": method,
        "reported_center": {"lat": event.get("lat"), "lon": event.get("lon")},
        "geometry": gj,
        "source_feature_count": feature_count,
        "display_simplification": {
            "applied": True,
            "tolerance_degrees": round(float(tolerance), 6),
            "purpose": "browser orientation only",
        },
        "scientific_note": (
            "This compact geometry is simplified for browser display. Exposure "
            "calculations use the unsimplified mapped footprint."
        ),
    }
    (OUT_DIR / file_name).write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {"status": "ready", "path": rel, "source": "GDACS", "method": method}


def enrich_list(
    events: list[dict[str, Any]],
    geometry_cache: dict[tuple, tuple],
    episode_cache: dict[tuple[str, str], int | None],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    diag = {"eligible": 0, "ready": 0, "missing": 0}
    for original in events:
        event = deepcopy(original)
        event.pop("footprint", None)
        desc = source_descriptor(event, episode_cache)
        if desc is None:
            out.append(event)
            continue
        diag["eligible"] += 1
        event_type, event_id, episode_id, requested_method = desc
        key = (event_type, event_id, episode_id)
        if key not in geometry_cache:
            try:
                geometry_cache[key] = fetch_geometry(event_type, event_id, episode_id)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN footprint {event_type} {event_id}: {type(exc).__name__}: {exc}")
                geometry_cache[key] = (None, 0, requested_method)
        geom, feature_count, resolved_method = geometry_cache[key]
        method = resolved_method or requested_method
        if geom is None or geom.is_empty:
            diag["missing"] += 1
            out.append(event)
            continue

        write_runtime_geometry(event, event_type, event_id, episode_id, method, geom)
        event["footprint"] = write_doc(
            event, event_type, event_id, episode_id, method, geom, feature_count
        )
        diag["ready"] += 1
        out.append(event)
    return out, diag


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    geometry_cache: dict[tuple, tuple] = {}
    episode_cache: dict[tuple[str, str], int | None] = {}

    if RUNTIME_DIR.exists():
        for p in RUNTIME_DIR.glob("*.json"):
            p.unlink()

    canonical, d1 = enrich_list(
        snap.get("canonical_events") or [], geometry_cache, episode_cache
    )
    display, d2 = enrich_list(
        snap.get("events") or [], geometry_cache, episode_cache
    )

    snap["canonical_events"] = canonical
    snap["events"] = display
    snap.setdefault("monitor", {})["event_footprints"] = {
        "source": "GDACS mapped polygons for wildfire, cyclone, drought and flood when available",
        "public_geometry": "topology-preserving simplified browser outline",
        "analysis_geometry": "unsimplified transient geometry used by same-run exposure enrichment",
        "no_polygon_behavior": "do not display a footprint panel",
    }
    snap["footprint_diagnostics"] = {
        "canonical": d1,
        "display": d2,
        "unique_source_geometries": len(geometry_cache),
    }

    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)
    print(json.dumps({
        "status": "ok",
        "canonical": d1,
        "display": d2,
        "unique_source_geometries": len(geometry_cache),
        "runtime_geometry_dir": str(RUNTIME_DIR.relative_to(ROOT)),
    }, indent=2))


if __name__ == "__main__":
    main()
