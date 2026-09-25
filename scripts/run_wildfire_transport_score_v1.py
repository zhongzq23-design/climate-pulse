#!/usr/bin/env python3
"""Compatibility runner for frozen WILDFIRE_TRANSPORT_SCORE_V1 events.

The frozen V4 development universe contains historical fires that can age out of
the rolling production event snapshot.  This runner keeps the core experiment
unchanged while reconstructing the minimal historical event record from the
frozen V4 artifact when needed.  It prefers the preserved QC-passed footprint
file on main; only if that file is unavailable does the existing GDACS fallback
remain available through the core loader.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import wildfire_transport_score_v1 as core


def historical_event_from_v4(v4_event_dir: Path, event_id: str) -> dict[str, Any]:
    source_doc = core.load(core.find_json(v4_event_dir, event_id))
    meta = source_doc.get("event") or {}
    try:
        lat = float(meta["lat"])
        lon = float(meta["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Frozen V4 artifact lacks coordinates for {event_id}") from exc

    gdacs_id = event_id.rsplit("-", 1)[-1]
    event: dict[str, Any] = {
        "id": event_id,
        "type": "Wildfire",
        "origin": "gdacs",
        "source_id": gdacs_id,
        "lat": lat,
        "lon": lon,
        "burned_area_ha": meta.get("burned_area_ha"),
        "last_detection": meta.get("last_detection"),
    }

    rel = f"data/footprints/{event_id}.json"
    footprint_path = core.p.ROOT / rel
    if footprint_path.exists():
        event["footprint"] = {
            "status": "ready",
            "path": rel,
            "qc_status": "pass",
            "method": "preserved_historical_qc_passed_footprint",
        }
        event["footprint_qc"] = {"status": "pass", "source": "preserved_historical_footprint"}
    return event


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event-id", required=True)
    ap.add_argument("--v4-event-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    original_find_event = core.p.find_event
    v4_event_dir = Path(args.v4_event_dir)

    def find_event_with_frozen_fallback(snapshot: dict[str, Any], event_id: str):
        try:
            return original_find_event(snapshot, event_id)
        except RuntimeError as exc:
            if "Event not found" not in str(exc):
                raise
            event = historical_event_from_v4(v4_event_dir, event_id)
            print(
                f"INFO frozen-event fallback: {event_id} "
                f"footprint={'preserved' if event.get('footprint') else 'live-gdacs-fallback'}",
                flush=True,
            )
            return event

    core.p.find_event = find_event_with_frozen_fallback
    return core.score_event(args)


if __name__ == "__main__":
    raise SystemExit(main())
