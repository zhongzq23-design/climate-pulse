#!/usr/bin/env python3
"""Add GDACS drought-footprint population exposure to Climate Pulse.

Drought is areal and long-lived, so no point-radius fallback is used. For a
canonical drought with a GDACS member, the latest GDACS drought event polygon is
overlaid with the authoritative JRC GHSL GHS-WUP-POP R2025A ~1 km population
source. The result is potential population exposure inside the mapped drought
footprint, not a count of people actually harmed or requiring assistance.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

# Import the compatibility runner first so invalid GDACS polygons are repaired
# with shapely.make_valid before union.
import run_hazard_enrichment  # noqa: F401
import enrich_hazard_exposure as h

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"


def enrich_drought(event, src):
    out = deepcopy(event)
    ids = h.gdacs_member_ids(event, "DR")
    if not ids:
        out["exposure_status"] = "drought_gdacs_polygon_unavailable"
        return out
    eid = ids[0]
    episode = h.latest_episode("DR", eid)
    feats = h.polygon_features("DR", eid, episode)
    geom = h.polygons_union(feats)
    if geom is None:
        out["exposure_status"] = "drought_gdacs_polygon_unavailable"
        return out
    pop = h.raster_population(src, geom)
    exposure = {
        "hazard": "drought",
        "gdacs_event_id": eid,
        "gdacs_episode_id": episode,
        "footprint_method": "GDACS drought event polygon",
        "population_in_drought_footprint": pop,
        "quality": "mapped_event_footprint",
        "interpretation": "Potential residential population exposure inside the mapped drought footprint; not observed impact or people actually affected.",
    }
    out["exposure"] = exposure
    out["exposure_status"] = "ready"
    h.write_exposure(out, exposure)
    return out


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    canonical = snap.get("canonical_events") or []
    pop_path = h.ensure_population_source()
    enriched = []
    diagnostics = {"drought_candidates": 0, "drought_enriched": 0, "drought_polygon_missing": 0}

    with h.rasterio.open(pop_path) as src:
        for event in canonical:
            if event.get("type") == "Drought":
                diagnostics["drought_candidates"] += 1
                e = enrich_drought(event, src)
                diagnostics["drought_enriched"] += int(isinstance(e.get("exposure"), dict) and e.get("exposure", {}).get("hazard") == "drought")
                diagnostics["drought_polygon_missing"] += int(e.get("exposure_status") == "drought_gdacs_polygon_unavailable")
            else:
                e = deepcopy(event)
            enriched.append(e)

    by_id = {str(e.get("id")): e for e in enriched}
    display = []
    for event in snap.get("events") or []:
        if event.get("type") == "Drought" and str(event.get("id")) in by_id:
            display.append(deepcopy(by_id[str(event.get("id"))]))
        else:
            display.append(event)

    snap["canonical_events"] = enriched
    snap["events"] = display
    hazard = snap.setdefault("hazard_exposure", {})
    hazard["drought_rule"] = "GHSL 2025 population inside the latest GDACS drought event polygon; no radius buffer"
    hazard["interpretation"] = "Potential population exposure; not observed impact, casualties, attribution, or loss"
    hazard.setdefault("diagnostics", {}).update(diagnostics)
    snap.setdefault("monitor", {})["drought_exposure_rule"] = hazard["drought_rule"]

    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)
    print(json.dumps({"status": "ok", "population_source": str(pop_path), **diagnostics}, indent=2))


if __name__ == "__main__":
    main()
