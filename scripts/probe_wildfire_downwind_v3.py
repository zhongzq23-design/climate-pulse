#!/usr/bin/env python3
"""V3 wildfire downwind screening wrapper.

Uses the V2 non-null FIRMS sampling fix and constrains the GEE active-fire
sampling region to the QC-passed wildfire footprint plus 20 km. This avoids
sampling unrelated fires in a broad 120-km circle and makes the source matching
explicitly fire-specific.

The operationally important rule in this wrapper is fail-closed behavior: if
fewer than three recent VIIRS active-fire detections can be associated with the
wildfire footprint after checking 2-, 4-, and 7-day windows, no downwind
population corridor is produced from an invented centroid source.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from shapely.geometry import mapping

import probe_wildfire_downwind as p
import probe_wildfire_downwind_v2 as v2


def collect_fire_seeds_footprint(
    ee: Any,
    event: dict[str, Any],
    perimeter,
    now,
    search_radius_km: float,
    max_seeds: int,
):
    try:
        region = ee.Geometry(mapping(perimeter)).buffer(20_000)
        region_method = "wildfire_footprint_plus_20km"
    except Exception:  # noqa: BLE001
        region = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(search_radius_km * 1000)
        region_method = "reported_center_radius_fallback"

    last_meta: dict[str, Any] = {}
    for days in (2, 4, 7):
        all_points = []
        sensors = []
        for dataset_id, label in p.FIRE_DATASETS:
            pts, meta = v2.fire_points_for_window_fixed(
                ee,
                dataset_id,
                label,
                region,
                now - timedelta(days=days),
                now + timedelta(days=1),
            )
            all_points.extend(pts)
            sensors.append(meta)
        selected, smeta = p.dedupe_select(all_points, perimeter, event, max_seeds)
        last_meta = {
            "search_window_days": days,
            "sampling_region_method": region_method,
            "matching_buffer_km": 20,
            "sensors": sensors,
            **smeta,
        }
        if len(selected) >= 3:
            return selected, last_meta

    raise RuntimeError(
        "FAIL_CLOSED_NO_RECENT_FIRMS_SEEDS: fewer than three recent nominal/high-confidence "
        "VIIRS detections matched the wildfire footprint +20 km after 2/4/7-day checks; "
        f"diagnostic={last_meta}"
    )


p.fire_points_for_window = v2.fire_points_for_window_fixed
p.load_event_perimeter = v2.load_event_perimeter_stored_first
p.collect_fire_seeds = collect_fire_seeds_footprint

if __name__ == "__main__":
    raise SystemExit(p.main())
