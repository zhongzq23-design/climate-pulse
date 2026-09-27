#!/usr/bin/env python3
"""Prospective Sentinel-5P CO interim comparison for one frozen wildfire batch.

The analysis contract is docs/WILDFIRE_TRANSPORT_PROSPECTIVE_S5P_CO_INTERIM_V1.md.
Predictions are never recomputed or retuned here: this script consumes only the
two geometries and metadata already frozen before outcome inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.affinity import rotate
from shapely.geometry import mapping, shape

import probe_wildfire_downwind as p

DATASET = "COPERNICUS/S5P/NRTI/L3_CO"
BAND = "CO_column_number_density"
UNITS = "mol m-2"
SCALE_M = 5000
COVERAGE_MIN = 0.50
BOOTSTRAP_N = 5000
ORIENTATIONS = {
    "downwind": 0.0,
    "crosswind_left": 90.0,
    "upwind": 180.0,
    "crosswind_right": -90.0,
}


def canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def parse_utc(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_freeze(doc: dict[str, Any]) -> list[dict[str, Any]]:
    if doc.get("experiment") != "WILDFIRE_TRANSPORT_PROSPECTIVE_V2":
        raise RuntimeError("unexpected freeze experiment")
    if doc.get("phase") != "prediction_freeze":
        raise RuntimeError("unexpected freeze phase")
    rows = [
        x for x in (doc.get("records") or [])
        if isinstance(x, dict) and x.get("prediction_sha256")
    ]
    if len(rows) != 12:
        raise RuntimeError(f"expected 12 frozen predictions, found {len(rows)}")
    for row in rows:
        expected = str(row["prediction_sha256"])
        copy = dict(row)
        copy.pop("prediction_sha256", None)
        actual = digest(copy)
        if actual != expected:
            raise RuntimeError(
                f"prediction hash mismatch for {(row.get('event') or {}).get('id')}: "
                f"expected={expected} actual={actual}"
            )
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


def masked_area_km2(ee: Any, valid_mask: Any, geom) -> float:
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


def reduce_mean(ee: Any, image: Any, geom) -> float | None:
    value = image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=ee.Geometry(mapping(geom)),
        scale=SCALE_M,
        bestEffort=True,
        maxPixels=100_000_000,
        tileScale=4,
    ).get("value").getInfo()
    return float(value) if value is not None else None


def image_for_window(
    ee: Any,
    start: datetime,
    end: datetime,
    event: dict[str, Any],
):
    domain = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(700_000)
    col = (
        ee.ImageCollection(DATASET)
        .filterDate(iso(start), iso(end))
        .filterBounds(domain)
    )
    count = int(col.size().getInfo() or 0)
    if count == 0:
        return None, None, {
            "image_count": 0,
            "first_image_time": None,
            "last_image_time": None,
        }
    times = [
        float(x)
        for x in (col.aggregate_array("system:time_start").getInfo() or [])
        if x is not None
    ]
    image = col.select(BAND).mean().rename("value")
    valid = image.mask().gt(0)
    return image, valid, {
        "image_count": count,
        "first_image_time": p.millis_to_iso(min(times)) if times else None,
        "last_image_time": p.millis_to_iso(max(times)) if times else None,
    }


def geometry_stats(
    ee: Any,
    image: Any,
    valid: Any,
    geom,
    event: dict[str, Any],
) -> dict[str, Any]:
    area = local_area_km2(geom, event)
    geoms = {
        label: rotate_geometry(geom, event, angle)
        for label, angle in ORIENTATIONS.items()
    }
    coverage = {}
    for label, oriented in geoms.items():
        valid_area = masked_area_km2(ee, valid, oriented)
        frac = min(1.0, valid_area / area) if area > 0 else 0.0
        coverage[label] = {
            "valid_area_km2": valid_area,
            "coverage_fraction": frac,
            "coverage_pass": bool(frac >= COVERAGE_MIN),
        }

    control_labels = [
        label
        for label in ("crosswind_left", "upwind", "crosswind_right")
        if coverage[label]["coverage_pass"]
    ]
    eligible = bool(
        coverage["downwind"]["coverage_pass"] and len(control_labels) >= 2
    )

    means = {}
    contrast = None
    control_mean = None
    if eligible:
        labels_to_reduce = ["downwind", *control_labels]
        for label in labels_to_reduce:
            means[label] = reduce_mean(ee, image, geoms[label])
        if means.get("downwind") is not None:
            control_values = [
                means[label]
                for label in control_labels
                if means.get(label) is not None
            ]
            if len(control_values) >= 2:
                control_mean = statistics.mean(control_values)
                contrast = float(means["downwind"]) - float(control_mean)

    return {
        "geometry_area_km2": area,
        "coverage_threshold": COVERAGE_MIN,
        "coverage": coverage,
        "orientation_eligible": bool(eligible and contrast is not None),
        "eligible_control_labels": control_labels,
        "downwind_mean": means.get("downwind"),
        "matched_control_mean": control_mean,
        "directional_contrast": contrast,
    }


def event_window(
    ee: Any,
    row: dict[str, Any],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    event = row["event"]
    image, valid, meta = image_for_window(ee, start, end, event)
    result = {
        "start": iso(start),
        "end": iso(end),
        **meta,
        "baseline": None,
        "transport_score": None,
        "both_geometry_eligible": False,
        "transport_minus_v4b": None,
    }
    if image is None or valid is None:
        return result

    baseline = shape(row["baseline"]["geometry"])
    scored = shape(row["transport_score"]["geometry"])
    b = geometry_stats(ee, image, valid, baseline, event)
    s = geometry_stats(ee, image, valid, scored, event)
    result["baseline"] = b
    result["transport_score"] = s
    eligible = bool(b["orientation_eligible"] and s["orientation_eligible"])
    result["both_geometry_eligible"] = eligible
    if eligible:
        result["transport_minus_v4b"] = (
            float(s["directional_contrast"]) - float(b["directional_contrast"])
        )
    return result


def qtile(vals: list[float], q: float) -> float:
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def bootstrap_median(values: list[float], seed: int) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    boots = [
        statistics.median(
            [values[rng.randrange(len(values))] for _ in values]
        )
        for _ in range(BOOTSTRAP_N)
    ]
    return [qtile(boots, 0.025), qtile(boots, 0.975)]


def complete_link_blocks(events: list[dict[str, Any]], threshold_km: float):
    clusters = [[e] for e in sorted(events, key=lambda x: str(x["id"]))]
    while True:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = max(
                    p.haversine_km(
                        [float(a["lon"]), float(a["lat"])],
                        [float(b["lon"]), float(b["lat"])],
                    )
                    for a in clusters[i]
                    for b in clusters[j]
                )
                if d <= threshold_km + 1e-9:
                    ids = "|".join(
                        sorted(str(x["id"]) for x in clusters[i] + clusters[j])
                    )
                    key = (round(d, 9), ids)
                    if best is None or key < best[0]:
                        best = (key, i, j)
        if best is None:
            break
        _, i, j = best
        merged = clusters[i] + clusters[j]
        clusters = [
            c for k, c in enumerate(clusters)
            if k not in (i, j)
        ] + [merged]
    return [
        sorted(str(x["id"]) for x in cluster)
        for cluster in clusters
    ]


def block_summary(
    eligible_rows: list[dict[str, Any]],
    threshold_km: float,
    seed: int,
) -> dict[str, Any]:
    events = [
        {
            "id": row["event_id"],
            "lat": row["lat"],
            "lon": row["lon"],
        }
        for row in eligible_rows
    ]
    blocks = complete_link_blocks(events, threshold_km)
    event_to_block = {
        eid: f"B{i+1:02d}"
        for i, ids in enumerate(blocks)
        for eid in ids
    }
    by_block: dict[str, list[float]] = defaultdict(list)
    for row in eligible_rows:
        by_block[event_to_block[row["event_id"]]].append(
            float(row["transport_minus_v4b"])
        )
    block_values = [
        statistics.median(vals)
        for _, vals in sorted(by_block.items())
        if vals
    ]
    return {
        "threshold_km": threshold_km,
        "blocks": blocks,
        "eligible_blocks": len(block_values),
        "positive_blocks": sum(x > 0 for x in block_values),
        "median_transport_minus_v4b": (
            statistics.median(block_values) if block_values else None
        ),
        "bootstrap_95ci": bootstrap_median(block_values, seed),
    }


def summarize_window(
    events: list[dict[str, Any]],
    window_key: str,
    seed: int,
) -> dict[str, Any]:
    rows = []
    for event in events:
        result = event["windows"][window_key]
        if not result["both_geometry_eligible"]:
            continue
        b = result["baseline"]
        s = result["transport_score"]
        rows.append(
            {
                "event_id": event["event_id"],
                "lat": event["lat"],
                "lon": event["lon"],
                "image_count": result["image_count"],
                "baseline_directional_contrast": b["directional_contrast"],
                "transport_score_directional_contrast": s["directional_contrast"],
                "transport_minus_v4b": result["transport_minus_v4b"],
                "baseline_downwind_coverage_fraction": b["coverage"]["downwind"]["coverage_fraction"],
                "score_downwind_coverage_fraction": s["coverage"]["downwind"]["coverage_fraction"],
            }
        )
    diffs = [float(x["transport_minus_v4b"]) for x in rows]
    return {
        "eligible_events": len(rows),
        "positive_improvement_events": sum(x > 0 for x in diffs),
        "negative_improvement_events": sum(x < 0 for x in diffs),
        "zero_improvement_events": sum(x == 0 for x in diffs),
        "median_transport_minus_v4b": (
            statistics.median(diffs) if diffs else None
        ),
        "bootstrap_95ci": bootstrap_median(diffs, seed),
        "spatial_block_sensitivity": {
            "500km": block_summary(rows, 500.0, seed + 500),
            "800km": block_summary(rows, 800.0, seed + 800),
        },
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freeze-json", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    freeze = load(Path(args.freeze_json))
    rows = validate_freeze(freeze)
    ee, _project = p.initialize_ee()

    events = []
    for row in rows:
        event = row["event"]
        start = parse_utc(row["ifs"]["window_start_valid_time"])
        end = parse_utc(row["ifs"]["window_end_valid_time"])
        day_start = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        events.append(
            {
                "event_id": event["id"],
                "lat": event["lat"],
                "lon": event["lon"],
                "prediction_sha256": row["prediction_sha256"],
                "windows": {
                    "exact_transport_window": event_window(
                        ee, row, start, end
                    ),
                    "transport_end_utc_day": event_window(
                        ee, row, day_start, day_end
                    ),
                },
            }
        )

    summary = {
        "exact_transport_window": summarize_window(
            events, "exact_transport_window", 2026092701
        ),
        "transport_end_utc_day": summarize_window(
            events, "transport_end_utc_day", 2026092702
        ),
    }

    doc = {
        "schema_version": "1.0",
        "experiment": "WILDFIRE_TRANSPORT_PROSPECTIVE_S5P_CO_INTERIM_V1",
        "status": "interim_precheckpoint_no_decision",
        "source_freeze": str(args.freeze_json),
        "source_freeze_frozen_at": freeze.get("frozen_at"),
        "dataset": DATASET,
        "band": BAND,
        "units": UNITS,
        "coverage_threshold": COVERAGE_MIN,
        "analysis_contract": "docs/WILDFIRE_TRANSPORT_PROSPECTIVE_S5P_CO_INTERIM_V1.md",
        "summary": summary,
        "events": events,
        "guardrail": (
            "First-batch interim diagnostic only. Formal prospective reporting "
            "checkpoints remain 30, 50 and 100 eligible fires. No tuning, "
            "stopping, event replacement or model-selection decision is "
            "permitted from this result."
        ),
    }
    dump(Path(args.output), doc)

    compact = {}
    for key, value in summary.items():
        compact[key] = {
            "eligible_events": value["eligible_events"],
            "positive_improvement_events": value["positive_improvement_events"],
            "median_transport_minus_v4b": value["median_transport_minus_v4b"],
            "bootstrap_95ci": value["bootstrap_95ci"],
            "blocks_500km": {
                "eligible_blocks": value["spatial_block_sensitivity"]["500km"]["eligible_blocks"],
                "positive_blocks": value["spatial_block_sensitivity"]["500km"]["positive_blocks"],
                "median": value["spatial_block_sensitivity"]["500km"]["median_transport_minus_v4b"],
                "ci": value["spatial_block_sensitivity"]["500km"]["bootstrap_95ci"],
            },
            "blocks_800km": {
                "eligible_blocks": value["spatial_block_sensitivity"]["800km"]["eligible_blocks"],
                "positive_blocks": value["spatial_block_sensitivity"]["800km"]["positive_blocks"],
                "median": value["spatial_block_sensitivity"]["800km"]["median_transport_minus_v4b"],
                "ci": value["spatial_block_sensitivity"]["800km"]["bootstrap_95ci"],
            },
        }
    print("PROSPECTIVE_S5P_CO_INTERIM_SUMMARY " + json.dumps(compact, separators=(",", ":")))
    for key, value in summary.items():
        for row in value["rows"]:
            print(
                "PROSPECTIVE_S5P_CO_INTERIM_EVENT "
                + json.dumps(
                    {
                        "window": key,
                        "event_id": row["event_id"],
                        "baseline_contrast": row["baseline_directional_contrast"],
                        "score_contrast": row["transport_score_directional_contrast"],
                        "transport_minus_v4b": row["transport_minus_v4b"],
                    },
                    separators=(",", ":"),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
