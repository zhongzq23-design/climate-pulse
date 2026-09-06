#!/usr/bin/env python3
"""Finalize wildfire visibility after current-episode synchronization.

Green wildfire source metrics can be rounded at the GDACS/GWIS presentation
layer. To avoid a threshold-edge event flickering out of Climate Pulse when the
source reports exactly 10,000 people within 5 km, Climate Pulse uses an inclusive
10,000-person threshold for public Green-wildfire visibility. Orange/Red remain
visible regardless of this Green threshold.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import enrich_hazard_exposure as h
from monitor_events import cluster_fires

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
MIN_HA = 10_000.0
MIN_POP_5KM = 10_000


def wildfire_eligible(event: dict[str, Any]) -> tuple[bool, str]:
    if event.get("type") != "Wildfire":
        return bool(event.get("display_eligible", True)), str(event.get("display_rule") or "")
    if event.get("priority") in {"Medium", "High"}:
        return True, "gdacs_orange_red"
    x = event.get("exposure") if isinstance(event.get("exposure"), dict) else {}
    try:
        eligible = float(event.get("burned_area_ha")) >= MIN_HA and int(x.get("population_within_5km")) >= MIN_POP_5KM
    except (TypeError, ValueError):
        eligible = False
    return eligible, "green_requires_current_episode_10000ha_and_population_within_5km_gte_10000"


def main() -> None:
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    canonical = []
    visible_wildfires = 0
    hidden_wildfires = 0
    for original in snap.get("canonical_events") or []:
        event = dict(original)
        if event.get("type") == "Wildfire":
            eligible, rule = wildfire_eligible(event)
            event["display_eligible"] = eligible
            event["display_rule"] = rule
            visible_wildfires += int(eligible)
            hidden_wildfires += int(not eligible)
        canonical.append(event)

    visible = [e for e in canonical if e.get("display_eligible", True)]
    display = cluster_fires(visible)
    for event in display:
        h.enrich_cluster_exposure(event)

    snap["canonical_events"] = canonical
    snap["events"] = display
    snap.setdefault("monitor", {})["wildfire_green_display_rule"] = (
        "current-episode burned_area_ha >= 10000 AND population_within_5km >= 10000"
    )
    snap["wildfire_display_diagnostics"] = {
        "visible_canonical_wildfires": visible_wildfires,
        "hidden_canonical_wildfires": hidden_wildfires,
        "green_boundary_policy": "inclusive at 10,000 people within 5 km to avoid source-rounding threshold flicker",
    }
    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)
    print(json.dumps({
        "status": "ok",
        "visible_canonical_wildfires": visible_wildfires,
        "hidden_canonical_wildfires": hidden_wildfires,
        "display_events": len(display),
    }, indent=2))


if __name__ == "__main__":
    main()
