#!/usr/bin/env python3
"""Persist compact browser-facing event footprints and transient analysis geometry.

The public geometry written under ``data/footprints`` is topology-preserving and
simplified for browser orientation. The unsimplified source geometry is written
only to ``.runtime/event_geometries`` for later exposure calculations in the
same GitHub Actions job and is never committed.

Floods receive a source-geometry context QC step so unrelated polygon resources
are not blindly unioned. Wildfires receive an admission QC step that verifies
GDACS/GWIS burned-area provenance, same-episode alignment, and coarse mapped-area
consistency before a perimeter can qualify for exposure-grade reporting.
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
from flood_footprint_qc import select_flood_geometry
from wildfire_footprint_qc import assess_wildfire_footprint, strict_wildfire_qc_pass

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
OUT_DIR = ROOT / "data" / "footprints"
RUNTIME_DIR = ROOT / ".runtime" / "event_geometries"
TYPE_CODE = {"Wildfire": "WF", "Storm": "TC", "Drought": "DR", "Flood": "FL"}
DEFAULT_METHOD = {
    "WF": "GDACS wildfire event polygon · pending wildfire admission QC",
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
    tolerance = min(0.05, max(0.0002, span / 600.0))
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


def fetch_geometry(
    event_type: str, event_id: str, episode_id: int | None,
    reported_lon: float | None = None, reported_lat: float | None = None,
):
    feats = h.polygon_features(event_type, event_id, episode_id)
    if not feats:
        return None, 0, DEFAULT_METHOD.get(event_type, "GDACS event polygon"), {
            "status": "failed", "reason": "no_source_features"
        }

    if event_type == "TC":
        geom, method = h.tc_wind_union(feats)
        return geom, len(feats), method, {"status": "not_applicable"}

    if event_type == "FL":
        try:
            lon = float(reported_lon)
            lat = float(reported_lat)
        except (TypeError, ValueError):
            return None, len(feats), DEFAULT_METHOD["FL"], {
                "status": "failed", "reason": "missing_reported_coordinate"
            }
        geom, qc = select_flood_geometry(feats, lon, lat)
        if qc.get("status") != "pass":
            return None, len(feats), DEFAULT_METHOD["FL"], qc
        return (
            geom,
            len(feats),
            "GDACS reported flood event/affected-area polygon · center-aligned context feature",
            qc,
        )

    geom = h.polygons_union(feats)
    method = DEFAULT_METHOD.get(event_type, "GDACS event polygon")
    return geom, len(feats), method, {"status": "not_applicable"}


def wildfire_qc_for_event(event: dict[str, Any], geom, episode_id: int | None) -> dict[str, Any]:
    exposure = event.get("exposure") if isinstance(event.get("exposure"), dict) else {}
    provenance = exposure.get("metric_provenance") if isinstance(exposure.get("metric_provenance"), dict) else {}
    burned_prov = provenance.get("burned_area_ha") if isinstance(provenance.get("burned_area_ha"), dict) else None
    burned_area = event.get("burned_area_ha")
    if burned_area is None:
        burned_area = exposure.get("burned_area_ha")
    return assess_wildfire_footprint(
        geom,
        source_burned_area_ha=burned_area,
        footprint_episode_id=episode_id,
        burned_area_provenance=burned_prov,
    )


def write_runtime_geometry(
    event: dict[str, Any], event_type: str, event_id: str,
    episode_id: int | None, method: str, geom, geometry_qc: dict[str, Any] | None,
) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "gdacs_event_type": event_type,
        "gdacs_event_id": event_id,
        "gdacs_episode_id": episode_id,
        "footprint_method": method,
        "geometry_qc": geometry_qc,
        "geometry": mapping(geom),
    }
    (RUNTIME_DIR / f"{safe_name(event.get('id'))}.json").write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def remove_public_doc(event: dict[str, Any]) -> None:
    p = OUT_DIR / f"{safe_name(event.get('id'))}.json"
    if p.exists():
        p.unlink()


def write_doc(
    event: dict[str, Any], event_type: str, event_id: str,
    episode_id: int | None, method: str, geom, feature_count: int,
    geometry_qc: dict[str, Any] | None,
) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    simplified, tolerance = display_geometry(geom)
    gj = mapping(simplified)
    gj["coordinates"] = round_coords(gj.get("coordinates"))

    file_name = safe_name(event.get("id")) + ".json"
    rel = f"data/footprints/{file_name}"
    doc = {
        "schema_version": "1.3",
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
        "geometry_qc": geometry_qc,
        "display_simplification": {
            "applied": True,
            "tolerance_degrees": round(float(tolerance), 6),
            "purpose": "browser orientation only",
        },
        "scientific_note": (
            "This compact geometry is simplified for browser display. Exposure calculations use the unsimplified mapped source footprint. "
            "Wildfire perimeter admission requires GDACS/GWIS source provenance, same-episode alignment and coarse area-consistency QC. "
            "Flood polygons remain reported event/affected-area context and are not interpreted as observed inundation."
        ),
    }
    (OUT_DIR / file_name).write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ready", "path": rel, "source": "GDACS", "method": method,
        "qc_status": (geometry_qc or {}).get("status"),
    }


def enrich_list(
    events: list[dict[str, Any]],
    geometry_cache: dict[tuple, tuple],
    episode_cache: dict[tuple[str, str], int | None],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    diag = {
        "eligible": 0, "ready": 0, "missing": 0,
        "flood_qc_pass": 0, "flood_qc_failed": 0,
        "wildfire_qc_pass": 0, "wildfire_qc_failed": 0,
    }
    for original in events:
        event = deepcopy(original)
        event.pop("footprint", None)
        event.pop("footprint_qc", None)
        desc = source_descriptor(event, episode_cache)
        if desc is None:
            out.append(event)
            continue
        diag["eligible"] += 1
        event_type, event_id, episode_id, requested_method = desc
        try:
            lat_key = round(float(event.get("lat")), 4)
            lon_key = round(float(event.get("lon")), 4)
        except (TypeError, ValueError):
            lat_key = lon_key = None
        key = (event_type, event_id, episode_id, lat_key, lon_key)
        if key not in geometry_cache:
            try:
                geometry_cache[key] = fetch_geometry(
                    event_type, event_id, episode_id,
                    event.get("lon"), event.get("lat"),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"WARN footprint {event_type} {event_id}: {type(exc).__name__}: {exc}")
                geometry_cache[key] = (
                    None, 0, requested_method,
                    {"status": "failed", "reason": f"exception:{type(exc).__name__}"},
                )
        geom, feature_count, resolved_method, geometry_qc = geometry_cache[key]
        method = resolved_method or requested_method

        if event_type == "WF" and geom is not None and not geom.is_empty:
            geometry_qc = wildfire_qc_for_event(event, geom, episode_id)
            if strict_wildfire_qc_pass(geometry_qc):
                method = "gdacs_gwis_episode_aligned_burned_area_perimeter_polygon"
                diag["wildfire_qc_pass"] += 1
            else:
                method = "gdacs_wildfire_event_polygon_unverified"
                diag["wildfire_qc_failed"] += 1
            event["footprint_qc"] = deepcopy(geometry_qc)

        if event_type == "FL":
            event["footprint_qc"] = deepcopy(geometry_qc)
            if (geometry_qc or {}).get("status") == "pass":
                diag["flood_qc_pass"] += 1
            else:
                diag["flood_qc_failed"] += 1

        if geom is None or geom.is_empty:
            diag["missing"] += 1
            remove_public_doc(event)
            out.append(event)
            continue

        write_runtime_geometry(
            event, event_type, event_id, episode_id, method, geom, geometry_qc
        )
        event["footprint"] = write_doc(
            event, event_type, event_id, episode_id, method, geom, feature_count,
            geometry_qc,
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
        "wildfire_qc": "exposure-grade admission requires GDACS/GWIS burned-area provenance, same-episode alignment, and a coarse mapped/source burned-area ratio between 0.5 and 2.0",
        "flood_qc": "select one center-aligned GDACS reported event/affected-area feature; retain it as context only and never infer inundation",
        "no_polygon_behavior": "do not display a footprint panel or polygon-derived exposure metric",
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
