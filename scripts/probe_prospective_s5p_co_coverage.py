#!/usr/bin/env python3
"""Availability-only Sentinel-5P CO coverage probe for a frozen prospective batch.

This script deliberately inspects only observation availability and valid-pixel
masks. It never reduces, prints, stores, thresholds, ranks, or compares CO
concentration values.

For each frozen event it checks two time windows:
1) the exact frozen IFS transport window;
2) the UTC calendar day containing the transport-window end.

For both frozen geometries (V4b baseline and locked transport-score geometry),
it reports valid-mask coverage for the downwind shape and same-shape rotations
(+90, 180, -90 deg). The historical matched-orientation coverage threshold
(>=50%) is reused strictly as an availability gate, not as an outcome metric.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.affinity import rotate
from shapely.geometry import mapping, shape

import probe_wildfire_downwind as p

DATASET = "COPERNICUS/S5P/NRTI/L3_CO"
BAND = "CO_column_number_density"
SCALE_M = 5000
COVERAGE_MIN = 0.50
ORIENTATIONS = {
    "downwind": 0.0,
    "crosswind_left": 90.0,
    "upwind": 180.0,
    "crosswind_right": -90.0,
}


def parse_utc(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_freeze(doc: dict[str, Any]) -> list[dict[str, Any]]:
    if doc.get("experiment") != "WILDFIRE_TRANSPORT_PROSPECTIVE_V2":
        raise RuntimeError("unexpected freeze experiment")
    if doc.get("phase") != "prediction_freeze":
        raise RuntimeError("unexpected freeze phase")
    rows = [x for x in (doc.get("records") or []) if isinstance(x, dict) and x.get("prediction_sha256")]
    if len(rows) != 12:
        raise RuntimeError(f"expected 12 frozen predictions, found {len(rows)}")
    for row in rows:
        event = row.get("event") or {}
        if not event.get("id"):
            raise RuntimeError("frozen event missing id")
        if not ((row.get("baseline") or {}).get("geometry")):
            raise RuntimeError(f"{event.get('id')}: baseline geometry missing")
        if not ((row.get("transport_score") or {}).get("geometry")):
            raise RuntimeError(f"{event.get('id')}: transport-score geometry missing")
        if not ((row.get("ifs") or {}).get("window_start_valid_time")):
            raise RuntimeError(f"{event.get('id')}: IFS window start missing")
        if not ((row.get("ifs") or {}).get("window_end_valid_time")):
            raise RuntimeError(f"{event.get('id')}: IFS window end missing")
    return rows


def rotate_geometry(geom, event: dict[str, Any], angle_deg: float):
    if angle_deg == 0:
        return geom
    fwd, inv = p.local_transformers(float(event["lat"]), float(event["lon"]))
    local = p.to_local(geom, fwd)
    return p.to_wgs84(rotate(local, angle_deg, origin=(0.0, 0.0)), inv)


def local_area_km2(geom, event: dict[str, Any]) -> float:
    fwd, _ = p.local_transformers(float(event["lat"]), float(event["lon"]))
    return float(p.to_local(geom, fwd).area / 1_000_000.0)


def valid_area_km2(ee: Any, valid_mask: Any, geom) -> float:
    value = (
        ee.Image.pixelArea()
        .updateMask(valid_mask)
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=ee.Geometry(mapping(geom)),
            scale=SCALE_M,
            bestEffort=True,
            maxPixels=100_000_000,
            tileScale=4,
        )
        .get("area")
        .getInfo()
    )
    return float(value or 0.0) / 1_000_000.0


def interval_meta(ee: Any, start: datetime, end: datetime, event: dict[str, Any]):
    center = ee.Geometry.Point([float(event["lon"]), float(event["lat"])])
    domain = center.buffer(700_000)
    col = (
        ee.ImageCollection(DATASET)
        .filterDate(iso(start), iso(end))
        .filterBounds(domain)
    )
    count = int(col.size().getInfo() or 0)
    if count == 0:
        return None, {
            "image_count": 0,
            "first_image_time": None,
            "last_image_time": None,
        }
    times = [float(x) for x in (col.aggregate_array("system:time_start").getInfo() or []) if x is not None]
    # Important: only the mask of the selected CO band is used downstream.
    # No CO concentration statistic is ever requested from Earth Engine.
    valid = col.select(BAND).mean().mask().gt(0)
    return valid, {
        "image_count": count,
        "first_image_time": p.millis_to_iso(min(times)) if times else None,
        "last_image_time": p.millis_to_iso(max(times)) if times else None,
    }


def geometry_coverage(ee: Any, valid_mask: Any, geom, event: dict[str, Any]):
    area = local_area_km2(geom, event)
    orientations = {}
    for label, angle in ORIENTATIONS.items():
        rotated = rotate_geometry(geom, event, angle)
        valid_area = valid_area_km2(ee, valid_mask, rotated)
        frac = min(1.0, valid_area / area) if area > 0 else 0.0
        orientations[label] = {
            "valid_area_km2": round(valid_area, 6),
            "coverage_fraction": round(frac, 6),
            "coverage_pass_50pct": bool(frac >= COVERAGE_MIN),
        }
    controls_pass = sum(
        bool(orientations[label]["coverage_pass_50pct"])
        for label in ("crosswind_left", "upwind", "crosswind_right")
    )
    return {
        "geometry_area_km2": round(area, 6),
        "orientations": orientations,
        "coverage_only_orientation_eligible": bool(
            orientations["downwind"]["coverage_pass_50pct"] and controls_pass >= 2
        ),
        "eligible_control_count": controls_pass,
    }


def check_event(ee: Any, row: dict[str, Any]):
    event = row["event"]
    baseline = shape(row["baseline"]["geometry"])
    scored = shape(row["transport_score"]["geometry"])
    start = parse_utc(row["ifs"]["window_start_valid_time"])
    end = parse_utc(row["ifs"]["window_end_valid_time"])
    day_start = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    windows = {
        "exact_transport_window": (start, end),
        "transport_end_utc_day": (day_start, day_end),
    }
    out = {
        "event_id": event["id"],
        "lat": event["lat"],
        "lon": event["lon"],
        "windows": {},
    }

    for label, (wstart, wend) in windows.items():
        valid, meta = interval_meta(ee, wstart, wend, event)
        item = {
            "start": iso(wstart),
            "end": iso(wend),
            **meta,
            "baseline": None,
            "transport_score": None,
            "both_geometry_coverage_eligible": False,
        }
        if valid is not None:
            base_cov = geometry_coverage(ee, valid, baseline, event)
            score_cov = geometry_coverage(ee, valid, scored, event)
            item["baseline"] = base_cov
            item["transport_score"] = score_cov
            item["both_geometry_coverage_eligible"] = bool(
                base_cov["coverage_only_orientation_eligible"]
                and score_cov["coverage_only_orientation_eligible"]
            )
        out["windows"][label] = item
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freeze-json", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    freeze = load(Path(args.freeze_json))
    rows = validate_freeze(freeze)
    ee, _project = p.initialize_ee()

    events = [check_event(ee, row) for row in rows]
    exact_ready = sum(
        bool(x["windows"]["exact_transport_window"]["both_geometry_coverage_eligible"])
        for x in events
    )
    day_ready = sum(
        bool(x["windows"]["transport_end_utc_day"]["both_geometry_coverage_eligible"])
        for x in events
    )
    doc = {
        "schema_version": "1.0",
        "probe": "PROSPECTIVE_S5P_CO_COVERAGE_ONLY",
        "source_freeze": str(args.freeze_json),
        "dataset": DATASET,
        "band_used_for_mask_only": BAND,
        "coverage_threshold": COVERAGE_MIN,
        "guardrail": (
            "Availability-only probe. No CO concentration values, means, percentiles, "
            "directional contrasts, ranks, or model-performance comparisons were read or emitted."
        ),
        "summary": {
            "frozen_event_count": len(events),
            "exact_transport_window_both_geometry_eligible": exact_ready,
            "transport_end_utc_day_both_geometry_eligible": day_ready,
        },
        "events": events,
    }
    Path(args.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "S5P_CO_COVERAGE_ONLY_SUMMARY "
        + json.dumps(doc["summary"], separators=(",", ":"))
    )
    for event in events:
        e = event["event_id"]
        exact = event["windows"]["exact_transport_window"]
        day = event["windows"]["transport_end_utc_day"]
        print(
            "S5P_CO_COVERAGE_ONLY_EVENT "
            + json.dumps(
                {
                    "event_id": e,
                    "exact_image_count": exact["image_count"],
                    "exact_both_geometry_eligible": exact["both_geometry_coverage_eligible"],
                    "day_image_count": day["image_count"],
                    "day_both_geometry_eligible": day["both_geometry_coverage_eligible"],
                },
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
