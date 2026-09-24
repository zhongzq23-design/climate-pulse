#!/usr/bin/env python3
"""Refresh expensive Climate Pulse event products only for changed canonical events.

This orchestrator is intentionally conservative: it reuses the already-tested
production enrichment scripts on a temporary mini snapshot containing only the
changed canonical events, then merges their outputs back into the full current
source snapshot.  Unchanged events inherit their previously published expensive
products.

The public/global display is rebuilt from the merged canonical universe at the
end, so removals and visibility changes are still reflected globally.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"

REUSABLE_FIELDS = (
    "exposure",
    "exposure_status",
    "footprint",
    "footprint_qc",
    "source_metrics",
    "display_eligible",
    "display_rule",
    "alert_level",
    "climate_context",
)
WILDFIRE_ALIGNED_FIELDS = (
    "burned_area_ha",
    "event_date",
    "last_detection",
    "summary",
)
PERSISTENT_TOP_LEVEL = (
    "hazard_exposure",
    "asset_exposure",
    "footprint_diagnostics",
    "wildfire_display_diagnostics",
)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected object JSON at {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ids(value: str) -> set[str]:
    return {x.strip() for x in str(value or "").split(",") if x.strip()}


def by_id(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(e.get("id")): e for e in events if isinstance(e, dict) and e.get("id") is not None}


def seed_unchanged_products(
    previous: dict[str, Any], current: dict[str, Any], changed_ids: set[str]
) -> dict[str, Any]:
    """Copy expensive published fields only for canonical events not being refreshed."""
    out = deepcopy(current)
    previous_events = by_id(previous.get("canonical_events") or [])
    seeded = []
    for original in out.get("canonical_events") or []:
        event = deepcopy(original)
        event_id = str(event.get("id"))
        old = previous_events.get(event_id)
        if old is not None and event_id not in changed_ids:
            for key in REUSABLE_FIELDS:
                if key in old:
                    event[key] = deepcopy(old[key])
            if event.get("type") == "Wildfire":
                for key in WILDFIRE_ALIGNED_FIELDS:
                    if key in old:
                        event[key] = deepcopy(old[key])
        seeded.append(event)
    out["canonical_events"] = seeded

    # Fresh source polling intentionally replaces top-level derived sections.
    # Restore those sections until their changed-event slices are recomputed.
    for key in PERSISTENT_TOP_LEVEL:
        if key not in out and key in previous:
            out[key] = deepcopy(previous[key])
    old_monitor = previous.get("monitor") if isinstance(previous.get("monitor"), dict) else {}
    new_monitor = out.get("monitor") if isinstance(out.get("monitor"), dict) else {}
    merged_monitor = deepcopy(old_monitor)
    merged_monitor.update(deepcopy(new_monitor))
    out["monitor"] = merged_monitor
    return out


def run_script(name: str, env: dict[str, str]) -> None:
    print(f"INCREMENTAL_EVENT_REFRESH running {name}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / name)], cwd=ROOT, env=env, check=True)


def gdacs_backed(event: dict[str, Any]) -> bool:
    if str(event.get("origin") or "").lower() == "gdacs":
        return True
    for key in ("source_members", "members"):
        values = event.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict) and gdacs_backed(value):
                    return True
    return False


def hazard_diagnostics(events: list[dict[str, Any]]) -> dict[str, int]:
    wildfires = [e for e in events if e.get("type") == "Wildfire"]
    storms = [e for e in events if e.get("type") == "Storm"]
    return {
        "wildfire_enriched": sum(isinstance(e.get("exposure"), dict) for e in wildfires),
        "wildfire_polygon_missing": sum(e.get("exposure_status") == "wildfire_polygon_unavailable" for e in wildfires),
        "storm_enriched": sum(isinstance(e.get("exposure"), dict) for e in storms),
        "storm_visible": sum(bool(e.get("display_eligible", True)) for e in storms),
        "storm_hidden_remote_or_unpopulated": sum(not bool(e.get("display_eligible", True)) for e in storms),
    }


def asset_diagnostics(events: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "drought_mapped": 0,
        "drought_landcover_ready": 0,
        "drought_crop_ready": 0,
        "wildfire_forest_ready": 0,
        "flood_population_ready": 0,
        "flood_gdp_ready": 0,
        "storm_gdp_ready": 0,
    }
    for event in events:
        x = event.get("exposure") if isinstance(event.get("exposure"), dict) else {}
        typ = event.get("type")
        if typ == "Drought":
            out["drought_mapped"] += int(x.get("mapped_footprint_area_km2") is not None)
            out["drought_landcover_ready"] += int(x.get("landcover_reference") is not None)
            out["drought_crop_ready"] += int(x.get("crop_reference") is not None)
        elif typ == "Wildfire":
            out["wildfire_forest_ready"] += int(x.get("forest_area_in_wildfire_footprint_km2") is not None)
        elif typ == "Flood":
            out["flood_population_ready"] += int(x.get("potentially_affected_population") is not None)
            out["flood_gdp_ready"] += int(x.get("potential_gdp_exposure_proxy_usd") is not None)
        elif typ == "Storm":
            out["storm_gdp_ready"] += int(x.get("potential_gdp_exposure_proxy_usd") is not None)
    return out


def footprint_diag(events: list[dict[str, Any]]) -> dict[str, int]:
    diag = {
        "eligible": 0,
        "ready": 0,
        "missing": 0,
        "flood_qc_pass": 0,
        "flood_qc_failed": 0,
        "wildfire_qc_pass": 0,
        "wildfire_qc_failed": 0,
    }
    for event in events:
        if event.get("type") not in {"Wildfire", "Storm", "Drought", "Flood"} or not gdacs_backed(event):
            continue
        diag["eligible"] += 1
        fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
        ready = fp.get("status") == "ready"
        diag["ready"] += int(ready)
        diag["missing"] += int(not ready)
        qc = event.get("footprint_qc") if isinstance(event.get("footprint_qc"), dict) else {}
        status = qc.get("status")
        if event.get("type") == "Flood" and qc:
            diag["flood_qc_pass"] += int(status == "pass")
            diag["flood_qc_failed"] += int(status != "pass")
        if event.get("type") == "Wildfire" and qc:
            diag["wildfire_qc_pass"] += int(status == "pass")
            diag["wildfire_qc_failed"] += int(status != "pass")
    return diag


def rewrite_matching_archive(snapshot: dict[str, Any]) -> None:
    value = str(snapshot.get("generated_at") or "").strip()
    if not value:
        return
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return
    path = (
        ROOT
        / "data"
        / "events"
        / "archive"
        / dt.strftime("%Y")
        / dt.strftime("%m")
        / dt.strftime("%d")
        / f"{dt.strftime('%H%M%S')}Z.json"
    )
    if path.exists():
        write_json(path, snapshot)


def refresh_global_diagnostics(snapshot: dict[str, Any]) -> dict[str, Any]:
    canonical = snapshot.get("canonical_events") or []
    display = snapshot.get("events") or []
    hz = snapshot.get("hazard_exposure") if isinstance(snapshot.get("hazard_exposure"), dict) else {}
    hz["diagnostics"] = hazard_diagnostics(canonical)
    snapshot["hazard_exposure"] = hz

    asset = snapshot.get("asset_exposure") if isinstance(snapshot.get("asset_exposure"), dict) else {}
    asset["diagnostics"] = asset_diagnostics(canonical)
    snapshot["asset_exposure"] = asset
    snapshot.setdefault("monitor", {})["asset_exposure"] = deepcopy(asset)

    d1 = footprint_diag(canonical)
    d2 = footprint_diag(display)
    snapshot["footprint_diagnostics"] = {
        "canonical": d1,
        "display": d2,
        "unique_source_geometries": d1["ready"],
        "incremental_refresh_note": "Diagnostics rebuilt from merged published event state; only changed source geometries were refetched this run.",
    }
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", default=str(LATEST))
    parser.add_argument("--changed-ids", default="")
    args = parser.parse_args()

    previous = load_json(Path(args.previous))
    current_path = Path(args.current)
    current = load_json(current_path)
    changed_ids = parse_ids(args.changed_ids)

    seeded = seed_unchanged_products(previous, current, changed_ids)
    current_ids = {str(e.get("id")) for e in seeded.get("canonical_events") or []}
    unknown = sorted(changed_ids - current_ids)
    if unknown:
        raise RuntimeError(f"incremental changed IDs are not in current canonical universe: {unknown}")

    target_events = [
        deepcopy(e)
        for e in seeded.get("canonical_events") or []
        if str(e.get("id")) in changed_ids
    ]

    env = os.environ.copy()
    cache = Path(env.get("CLIMATE_PULSE_CACHE_DIR") or (Path.home() / ".cache" / "climate-pulse")).expanduser()
    env["CLIMATE_PULSE_CACHE_DIR"] = str(cache)

    mini_final: dict[str, Any] | None = None
    if target_events:
        mini = deepcopy(seeded)
        mini["canonical_events"] = target_events
        mini["events"] = deepcopy(target_events)
        write_json(current_path, mini)
        for script in (
            "run_hazard_enrichment.py",
            "sync_wildfire_episode_metrics.py",
            "finalize_wildfire_display.py",
            "enrich_event_footprints.py",
            "enrich_asset_exposure.py",
        ):
            run_script(script, env)
        mini_final = load_json(current_path)

    merged = deepcopy(seeded)
    if mini_final is not None:
        processed = by_id(mini_final.get("canonical_events") or [])
        missing = sorted(changed_ids - set(processed))
        if missing:
            raise RuntimeError(f"changed canonical events disappeared during incremental enrichment: {missing}")
        merged["canonical_events"] = [
            deepcopy(processed.get(str(e.get("id")), e))
            for e in seeded.get("canonical_events") or []
        ]
        if mini_final.get("schema_version"):
            merged["schema_version"] = mini_final["schema_version"]
        for key in ("hazard_exposure", "asset_exposure"):
            if isinstance(mini_final.get(key), dict):
                merged[key] = deepcopy(mini_final[key])
        if isinstance(mini_final.get("monitor"), dict):
            monitor = merged.setdefault("monitor", {})
            monitor.update(deepcopy(mini_final["monitor"]))

    write_json(current_path, merged)
    # Rebuild visibility/display globally after the changed canonical products are merged.
    run_script("finalize_wildfire_display.py", env)
    final = refresh_global_diagnostics(load_json(current_path))
    final.setdefault("monitor", {})["incremental_event_refresh"] = {
        "version": "INCREMENTAL_EVENT_REFRESH_V1",
        "changed_canonical_event_count": len(changed_ids),
        "changed_canonical_event_ids": sorted(changed_ids),
        "strategy": "reuse unchanged published products; run existing expensive event enrichers only on changed canonical events; rebuild global display afterward",
    }
    write_json(current_path, final)
    rewrite_matching_archive(final)

    print(json.dumps({
        "status": "ok",
        "mode": "incremental",
        "changed_event_count": len(changed_ids),
        "changed_event_ids": sorted(changed_ids),
        "canonical_events": len(final.get("canonical_events") or []),
        "display_events": len(final.get("events") or []),
        "cache_root": str(cache),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
