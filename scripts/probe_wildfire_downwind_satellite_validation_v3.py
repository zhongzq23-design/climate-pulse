#!/usr/bin/env python3
"""Satellite Validation V2: matched-orientation observational validation.

This wrapper replaces the broad 700-km plume-centroid direction diagnostic with
same-shape, same-area orientation controls.  The IFS-predicted downwind screen is
compared with copies rotated +90, 180 and -90 degrees about the reported fire
center.  CAMS/GFAS are not used as validation truth.

Primary per-sensor evidence is the contrast between the downwind mean and the
mean of adequately observed matched controls.  A sensor is orientation-eligible
only when the downwind corridor has >=50% valid coverage and at least two of the
three controls also have >=50% valid coverage.  This is particularly important
for cloud-sensitive MAIAC AOD.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

from shapely.affinity import rotate
from shapely.geometry import mapping

import probe_wildfire_downwind_satellite_validation as v1

COVERAGE_MIN = 0.50
ORIGINAL_MAKE_GEOJSON = v1.make_geojson


def rotate_geometry(geom, event: dict[str, Any], angle_deg: float):
    fwd, inv = v1.p.local_transformers(float(event["lat"]), float(event["lon"]))
    local = v1.p.to_local(geom, fwd)
    return v1.p.to_wgs84(rotate(local, angle_deg, origin=(0.0, 0.0)), inv)


def _orientation_stats(ee: Any, image: Any, valid: Any, geom, area_km2: float, scale_m: int):
    valid_area = v1.masked_area_km2(ee, valid, geom, scale_m)
    coverage = min(1.0, valid_area / area_km2) if area_km2 > 0 else 0.0
    mean = v1.reduce_mean(ee, image, geom, scale_m) if valid_area > 0 else None
    return {
        "valid_area_km2": valid_area,
        "coverage_fraction": coverage,
        "mean": mean,
        "coverage_pass": bool(coverage >= COVERAGE_MIN and mean is not None),
    }


def score_satellite_orientation(
    ee: Any,
    name: str,
    cfg: dict[str, Any],
    event: dict[str, Any],
    target_day,
    obs,
    predicted,
    upwind,
    model_dir: dict[str, Any] | None,
):
    image, meta = v1.satellite_image(ee, cfg, event, target_day, obs)
    if image is None:
        return {"status": "no_data", "dataset": cfg["dataset"], **meta}

    left = rotate_geometry(predicted, event, 90.0)
    right = rotate_geometry(predicted, event, -90.0)
    area_km2 = v1.local_area_km2(predicted, event)
    valid = image.mask().gt(0)
    scale_m = int(cfg["scale_m"])
    geoms = {
        "downwind": predicted,
        "crosswind_left": left,
        "upwind": upwind,
        "crosswind_right": right,
    }
    orientations = {
        label: _orientation_stats(ee, image, valid, geom, area_km2, scale_m)
        for label, geom in geoms.items()
    }

    down = orientations["downwind"]
    valid_controls = [
        orientations[label]["mean"]
        for label in ("crosswind_left", "upwind", "crosswind_right")
        if orientations[label]["coverage_pass"]
    ]
    eligible = bool(down["coverage_pass"] and len(valid_controls) >= 2)
    control_mean = statistics.mean(valid_controls) if valid_controls else None
    difference = (
        float(down["mean"]) - float(control_mean)
        if eligible and down["mean"] is not None and control_mean is not None
        else None
    )
    ratio = (
        float(down["mean"]) / float(control_mean)
        if difference is not None and control_mean is not None and float(control_mean) > 0
        else None
    )

    comparison_means = {
        label: float(stats["mean"])
        for label, stats in orientations.items()
        if stats["coverage_pass"] and stats["mean"] is not None
    }
    downwind_is_max = None
    rank = None
    if eligible and "downwind" in comparison_means:
        down_value = comparison_means["downwind"]
        downwind_is_max = bool(down_value >= max(comparison_means.values()))
        rank = 1 + sum(1 for value in comparison_means.values() if value > down_value)

    up = orientations["upwind"]
    down_minus_up = (
        float(down["mean"]) - float(up["mean"])
        if down["coverage_pass"] and up["coverage_pass"] and down["mean"] is not None and up["mean"] is not None
        else None
    )
    down_to_up = (
        float(down["mean"]) / float(up["mean"])
        if down_minus_up is not None and up["mean"] is not None and float(up["mean"]) > 0
        else None
    )

    return {
        "status": "ok",
        "validation_version": "matched_orientation_v2",
        "dataset": cfg["dataset"],
        "band": cfg["band"],
        "image_count": meta.get("image_count"),
        "first_image_time": meta.get("first_image_time"),
        "last_image_time": meta.get("last_image_time"),
        "coverage_threshold": COVERAGE_MIN,
        "predicted_corridor_area_km2": area_km2,
        "orientations": orientations,
        "orientation_eligible": eligible,
        "eligible_control_count": len(valid_controls),
        "matched_control_mean": control_mean,
        "downwind_minus_control_mean": difference,
        "downwind_to_control_mean_ratio": ratio,
        "downwind_is_max_orientation": downwind_is_max,
        "downwind_rank_among_eligible_orientations": rank,
        "directional_support": bool(difference is not None and difference > 0),
        "predicted_valid_coverage_fraction": down["coverage_fraction"],
        "matched_upwind_mean": up["mean"],
        "downwind_mean": down["mean"],
        "downwind_minus_upwind": down_minus_up,
        "downwind_to_upwind_ratio": down_to_up,
        "model_observed_bearing_difference_deg": None,
    }


def make_geojson_v2(event, perimeter, seeds, tracks, predicted, upwind):
    doc = ORIGINAL_MAKE_GEOJSON(event, perimeter, seeds, tracks, predicted, upwind)
    doc["features"].insert(3, {
        "type": "Feature",
        "geometry": mapping(rotate_geometry(predicted, event, 90.0)),
        "properties": {"kind": "matched_crosswind_left_control", "rotation_deg": 90},
    })
    doc["features"].insert(4, {
        "type": "Feature",
        "geometry": mapping(rotate_geometry(predicted, event, -90.0)),
        "properties": {"kind": "matched_crosswind_right_control", "rotation_deg": -90},
    })
    return doc


def _output_dir_from_argv() -> Path:
    if "--output-dir" in sys.argv:
        idx = sys.argv.index("--output-dir")
        if idx + 1 < len(sys.argv):
            return Path(sys.argv[idx + 1])
    return Path("artifacts/wildfire_satellite_validation")


def rewrite_summary(out_dir: Path) -> None:
    path = out_dir / "summary.json"
    if not path.exists():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema_version"] = "2.0"
    doc["experiment"] = "wildfire_downwind_satellite_validation_v2_matched_orientation"
    doc["metric_notes"] = {
        "primary_directional_test": (
            "Compare the IFS-predicted downwind corridor with same-shape, same-area copies rotated "
            "+90, 180 and -90 degrees around the fire center. Primary contrast is downwind mean minus "
            "the mean of adequately observed controls."
        ),
        "coverage_rule": (
            "A sensor is orientation-eligible only when downwind valid coverage is >=50% and at least "
            "two of the three matched controls also have >=50% valid coverage."
        ),
        "why_changed": (
            "The former 700-km top-20% centroid direction metric was dropped because unrelated fires, "
            "dust and regional aerosol gradients can dominate a broad-domain centroid."
        ),
        "limitations": [
            "AAI responds to dust/ash as well as smoke.",
            "CO includes combustion and atmospheric-background sources beyond the focal wildfire.",
            "MAIAC AOD is cloud-sensitive; low-coverage cases fail the primary coverage gate.",
            "Matched-orientation evidence validates directional screening geometry, not surface PM2.5 dose or health impact.",
        ],
    }
    for item in doc.get("events") or []:
        scores = item.get("satellite_validation") or {}
        eligible = [name for name, score in scores.items() if score.get("orientation_eligible")]
        positive = [name for name in eligible if scores[name].get("directional_support")]
        maxima = [name for name in eligible if scores[name].get("downwind_is_max_orientation")]
        item["diagnostic_summary_v2"] = {
            "orientation_eligible_sensors": eligible,
            "positive_directional_contrast_sensors": positive,
            "downwind_is_max_orientation_sensors": maxima,
            "positive_sensor_count": len(positive),
            "eligible_sensor_count": len(eligible),
            "note": "These are diagnostic observational comparisons, not a calibrated smoke-confidence score.",
        }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


v1.score_satellite = score_satellite_orientation
v1.make_geojson = make_geojson_v2

if __name__ == "__main__":
    out_dir = _output_dir_from_argv()
    rc = v1.main()
    rewrite_summary(out_dir)
    raise SystemExit(rc)
