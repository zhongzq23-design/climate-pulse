#!/usr/bin/env python3
"""V2 wrapper for the experimental wildfire downwind screening pilot.

Changes from v1:
- sample only non-null VIIRS fire pixels (avoids the arbitrary 5,000-cell cap
  being consumed by masked/non-fire pixels);
- prefer the already-QC-passed stored wildfire footprint for fire-source
  matching, avoiding a fragile live GDACS polygon union during this probe.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import shape

import probe_wildfire_downwind as p


def fire_points_for_window_fixed(
    ee: Any,
    dataset_id: str,
    label: str,
    region: Any,
    start,
    end,
):
    col = ee.ImageCollection(dataset_id).filterDate(p.iso(start), p.iso(end)).filterBounds(region)
    image_count = int(col.size().getInfo() or 0)
    latest_ms = col.aggregate_max("system:time_start").getInfo() if image_count else None
    if not image_count:
        return [], {
            "dataset": dataset_id,
            "label": label,
            "image_count": 0,
            "latest_image_time": None,
            "sampled_fire_pixels": 0,
        }

    confidence = col.select("confidence").max().rename("confidence")
    frp = col.select("frp").max().rename("frp")
    acq = col.select("acq_epoch").max().rename("acq_epoch")
    composite = confidence.addBands(frp).addBands(acq).updateMask(confidence.gte(1))
    fc = composite.sample(
        region=region,
        scale=375,
        geometries=True,
        dropNulls=True,
        tileScale=4,
    ).limit(5000)

    points = []
    for feat in (fc.getInfo() or {}).get("features", []):
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        props = feat.get("properties") or {}
        if len(coords) < 2:
            continue
        try:
            acq_epoch = float(props["acq_epoch"]) if props.get("acq_epoch") is not None else None
            points.append({
                "lon": float(coords[0]),
                "lat": float(coords[1]),
                "confidence": int(round(float(props.get("confidence") or 0))),
                "frp_mw": float(props["frp"]) if props.get("frp") is not None else None,
                "acq_epoch": acq_epoch,
                "acq_time": p.seconds_to_iso(acq_epoch),
                "sensor": label,
                "dataset": dataset_id,
            })
        except (TypeError, ValueError):
            continue

    return points, {
        "dataset": dataset_id,
        "label": label,
        "image_count": image_count,
        "latest_image_time": p.millis_to_iso(latest_ms),
        "sampled_fire_pixels": len(points),
    }


def load_event_perimeter_stored_first(event: dict[str, Any]):
    fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
    rel = fp.get("path")
    if rel and fp.get("qc_status") == "pass":
        path = p.ROOT / str(rel)
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            geom = shape(doc.get("geometry"))
            if not geom.is_empty:
                return geom, "stored_qc_passed_simplified_footprint", doc.get("gdacs_episode_id")
    return p.load_event_perimeter(event)


p.fire_points_for_window = fire_points_for_window_fixed
p.load_event_perimeter = load_event_perimeter_stored_first

if __name__ == "__main__":
    raise SystemExit(p.main())
