#!/usr/bin/env python3
"""Observation-only validation of the experimental wildfire downwind screen.

This probe deliberately does not use CAMS/GFAS as validation truth. It selects
recent, geographically separated, large wildfires using only the production
snapshot metadata, reconstructs the provisional 100 m IFS / 50 km / <=12 h
Downwind Screening corridor for the latest UTC day shared by Sentinel-5P AAI,
Sentinel-5P CO and MODIS MAIAC AOD, then scores the corridor against those
satellite observations.

The output is diagnostic validation evidence only. AAI can respond to dust and
other absorbing aerosols, CO has non-fire sources, and MAIAC AOD is cloud-
sensitive. No single satellite layer is treated as wildfire-smoke truth.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.affinity import rotate
from shapely.geometry import LineString, mapping
from shapely.ops import unary_union

import probe_wildfire_downwind as p
import probe_wildfire_downwind_v2 as v2

AAI_DATASET = "COPERNICUS/S5P/NRTI/L3_AER_AI"
CO_DATASET = "COPERNICUS/S5P/NRTI/L3_CO"
MAIAC_DATASET = "MODIS/061/MCD19A2_GRANULES"
IFS_DATASET = p.IFS_DATASET

SATELLITES = {
    "s5p_aai": {
        "dataset": AAI_DATASET,
        "band": "absorbing_aerosol_index",
        "scale_m": 5000,
        "window_hours": 4,
        "transform": "identity",
    },
    "s5p_co": {
        "dataset": CO_DATASET,
        "band": "CO_column_number_density",
        "scale_m": 5000,
        "window_hours": 4,
        "transform": "identity",
    },
    "maiac_aod047": {
        "dataset": MAIAC_DATASET,
        "band": "Optical_Depth_047",
        "scale_m": 2000,
        "window_hours": None,
        "transform": "maiac_aod047",
    },
}


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def dt_from_ms(value: Any) -> datetime:
    return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)


def day_from_ms(value: Any) -> date:
    return dt_from_ms(value).date()


def latest_dataset_day(ee: Any, dataset: str) -> date:
    value = ee.ImageCollection(dataset).aggregate_max("system:time_start").getInfo()
    if value is None:
        raise RuntimeError(f"No system:time_start available for {dataset}")
    return day_from_ms(value)


def common_satellite_day(ee: Any, override: str | None) -> tuple[date, dict[str, str]]:
    if override:
        day = date.fromisoformat(override)
        return day, {k: str(day) for k in SATELLITES}
    latest = {name: latest_dataset_day(ee, cfg["dataset"]) for name, cfg in SATELLITES.items()}
    return min(latest.values()), {k: str(v) for k, v in latest.items()}


def canonical_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    values = snapshot.get("canonical_events") or snapshot.get("events") or []
    return [x for x in values if isinstance(x, dict)]


def event_active_on(event: dict[str, Any], day: date) -> bool:
    start, end = day_bounds(day)
    event_start = p.parse_dt(event.get("event_start"))
    last = p.parse_dt(event.get("last_detection") or event.get("event_end"))
    if event_start and event_start >= end:
        return False
    if last and last < start:
        return False
    return True


def select_events(snapshot: dict[str, Any], target_day: date, max_events: int, min_sep_km: float):
    candidates = []
    for event in canonical_events(snapshot):
        fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
        qc = event.get("footprint_qc") if isinstance(event.get("footprint_qc"), dict) else {}
        if str(event.get("type")) != "Wildfire":
            continue
        if fp.get("status") != "ready" or fp.get("qc_status") != "pass" or qc.get("status") != "pass":
            continue
        try:
            area = float(event.get("burned_area_ha") or 0)
            float(event["lat"]); float(event["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if area < 10_000 or not event_active_on(event, target_day):
            continue
        candidates.append(event)
    candidates.sort(key=lambda x: float(x.get("burned_area_ha") or 0), reverse=True)

    selected: list[dict[str, Any]] = []
    for sep in (float(min_sep_km), 500.0, 250.0, 0.0):
        selected = []
        for event in candidates:
            here = [float(event["lon"]), float(event["lat"])]
            if all(p.haversine_km(here, [float(x["lon"]), float(x["lat"])]) >= sep for x in selected):
                selected.append(event)
            if len(selected) >= max_events:
                break
        if len(selected) >= max_events:
            return selected, len(candidates), sep
    return selected, len(candidates), 0.0


def load_perimeter(event: dict[str, Any]):
    return v2.load_event_perimeter_stored_first(event)


def collect_seeds_for_day(ee: Any, event: dict[str, Any], perimeter, target_day: date, max_seeds: int):
    day_start, day_end = day_bounds(target_day)
    region = ee.Geometry(mapping(perimeter)).buffer(20_000)
    last_meta: dict[str, Any] = {}
    for lookback_days in (1, 2, 4):
        all_points = []
        sensors = []
        start = day_start - timedelta(days=lookback_days)
        for dataset_id, label in p.FIRE_DATASETS:
            pts, meta = v2.fire_points_for_window_fixed(ee, dataset_id, label, region, start, day_end)
            all_points.extend(pts)
            sensors.append(meta)
        selected, smeta = p.dedupe_select(all_points, perimeter, event, max_seeds)
        last_meta = {
            "lookback_days": lookback_days,
            "window_start": p.iso(start),
            "window_end": p.iso(day_end),
            "sampling_region": "qc_passed_wildfire_footprint_plus_20km",
            "sensors": sensors,
            **smeta,
        }
        if len(selected) >= 3:
            return selected, last_meta
    raise RuntimeError(f"fewer than three VIIRS seeds for target day; diagnostic={last_meta}")


def observation_time(ee: Any, event: dict[str, Any], target_day: date) -> tuple[datetime, int]:
    start, end = day_bounds(target_day)
    region = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(700_000)
    col = ee.ImageCollection(AAI_DATASET).filterDate(p.iso(start), p.iso(end)).filterBounds(region)
    times = sorted(float(x) for x in (col.aggregate_array("system:time_start").getInfo() or []) if x is not None)
    if not times:
        return start + timedelta(hours=12), 0
    return dt_from_ms(times[len(times) // 2]), len(times)


def choose_ifs_run(ee: Any, obs: datetime, max_horizon_hours: int):
    col = ee.ImageCollection(IFS_DATASET)
    obs_ms = int(obs.timestamp() * 1000)
    lead0 = (
        col.filter(ee.Filter.eq("forecast_hours", 0))
        .filter(ee.Filter.gte("forecast_time", obs_ms - 18 * 3_600_000))
        .filter(ee.Filter.lte("forecast_time", obs_ms - 2 * 3_600_000))
    )
    base_ms = lead0.aggregate_max("forecast_time").getInfo()
    if base_ms is None:
        raise RuntimeError(f"No IFS lead-0 cycle available before {p.iso(obs)}")
    base_image = ee.Image(lead0.filter(ee.Filter.eq("forecast_time", base_ms)).first())
    creation_ms = base_image.get("creation_time").getInfo()
    if creation_ms is None:
        raise RuntimeError("IFS lead-0 image missing creation_time")
    run = col.filter(ee.Filter.eq("creation_time", creation_ms)).sort("forecast_hours")
    available = sorted({int(x) for x in (run.aggregate_array("forecast_hours").getInfo() or [])})
    available = [x for x in available if 0 <= x <= max_horizon_hours]
    if len(available) < 2:
        raise RuntimeError(f"Insufficient IFS leads: {available}")
    target_hours = max(0.0, (obs_ms - float(base_ms)) / 3_600_000.0)
    positive = [x for x in available if x > 0]
    target_lead = min(positive or available, key=lambda x: abs(float(x) - target_hours))
    leads = [x for x in available if x <= target_lead]
    if len(leads) < 2:
        leads = available[:2]
        target_lead = leads[-1]
    return run, int(creation_ms), int(base_ms), leads, target_hours


def simulate_100m(ee: Any, run: Any, leads: list[int], seeds: list[dict[str, Any]]):
    tracks = {str(i): [[float(s["lon"]), float(s["lat"])]] for i, s in enumerate(seeds)}
    speeds = []
    for idx, lead in enumerate(leads[:-1]):
        next_lead = leads[idx + 1]
        dt_seconds = (next_lead - lead) * 3600.0
        image = ee.Image(run.filter(ee.Filter.eq("forecast_hours", int(lead))).first())
        positions = [
            {"track_id": tid, "lon": coords[-1][0], "lat": coords[-1][1]}
            for tid, coords in tracks.items()
        ]
        winds = p.sample_wind(ee, image, positions)
        for tid, coords in tracks.items():
            w = winds.get(tid)
            if not w:
                coords.append(list(coords[-1]))
                continue
            u = float(w["u_component_of_wind_100m_sfc"])
            v = float(w["v_component_of_wind_100m_sfc"])
            speeds.append(math.hypot(u, v))
            coords.append(list(p.advect(coords[-1][0], coords[-1][1], u, v, dt_seconds)))
    return tracks, p.stats(speeds)


def mirror_geometry(geom, event: dict[str, Any]):
    fwd, inv = p.local_transformers(float(event["lat"]), float(event["lon"]))
    local = p.to_local(geom, fwd)
    return p.to_wgs84(rotate(local, 180.0, origin=(0.0, 0.0)), inv)


def local_area_km2(geom, event: dict[str, Any]) -> float:
    fwd, _ = p.local_transformers(float(event["lat"]), float(event["lon"]))
    return float(p.to_local(geom, fwd).area / 1_000_000.0)


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def model_direction(event: dict[str, Any], tracks: dict[str, list[list[float]]]):
    ends = [coords[-1] for coords in tracks.values() if coords]
    if not ends:
        return None
    lon = sum(x[0] for x in ends) / len(ends)
    lat = sum(x[1] for x in ends) / len(ends)
    return {
        "terminal_centroid": {"lon": lon, "lat": lat},
        "bearing_deg": bearing_deg(float(event["lon"]), float(event["lat"]), lon, lat),
    }


def satellite_image(ee: Any, cfg: dict[str, Any], event: dict[str, Any], target_day: date, obs: datetime):
    start, end = day_bounds(target_day)
    region = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(700_000)
    col = ee.ImageCollection(cfg["dataset"]).filterBounds(region)
    if cfg.get("window_hours"):
        hours = float(cfg["window_hours"])
        narrow = col.filterDate(p.iso(obs - timedelta(hours=hours)), p.iso(obs + timedelta(hours=hours)))
        if int(narrow.size().getInfo() or 0) > 0:
            col = narrow
        else:
            col = col.filterDate(p.iso(start), p.iso(end))
    else:
        col = col.filterDate(p.iso(start), p.iso(end))
    count = int(col.size().getInfo() or 0)
    if count == 0:
        return None, {"image_count": 0}
    image = col.select(cfg["band"]).mean().rename("value")
    if cfg.get("transform") == "maiac_aod047":
        image = image.updateMask(image.gte(0).And(image.lte(8000))).multiply(0.001).rename("value")
    times = [float(x) for x in (col.aggregate_array("system:time_start").getInfo() or []) if x is not None]
    return image, {
        "image_count": count,
        "first_image_time": p.millis_to_iso(min(times)) if times else None,
        "last_image_time": p.millis_to_iso(max(times)) if times else None,
    }


def reduce_mean(ee: Any, image: Any, geom, scale_m: int):
    value = image.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=ee.Geometry(mapping(geom)), scale=scale_m,
        bestEffort=True, maxPixels=100_000_000, tileScale=4,
    ).get("value").getInfo()
    return float(value) if value is not None else None


def masked_area_km2(ee: Any, mask: Any, geom, scale_m: int):
    value = ee.Image.pixelArea().updateMask(mask).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=ee.Geometry(mapping(geom)), scale=scale_m,
        bestEffort=True, maxPixels=100_000_000, tileScale=4,
    ).get("area").getInfo()
    return float(value or 0.0) / 1_000_000.0


def high_centroid(ee: Any, high_mask: Any, domain: Any, scale_m: int):
    ll = ee.Image.pixelLonLat().updateMask(high_mask)
    values = ll.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=domain, scale=scale_m,
        bestEffort=True, maxPixels=100_000_000, tileScale=4,
    ).getInfo() or {}
    if values.get("longitude") is None or values.get("latitude") is None:
        return None
    return {"lon": float(values["longitude"]), "lat": float(values["latitude"])}


def score_satellite(
    ee: Any,
    name: str,
    cfg: dict[str, Any],
    event: dict[str, Any],
    target_day: date,
    obs: datetime,
    predicted,
    upwind,
    model_dir: dict[str, Any] | None,
):
    image, meta = satellite_image(ee, cfg, event, target_day, obs)
    if image is None:
        return {"status": "no_data", "dataset": cfg["dataset"], **meta}

    domain = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(700_000)
    valid = image.mask().gt(0)
    percentile = image.reduceRegion(
        reducer=ee.Reducer.percentile([80]), geometry=domain, scale=int(cfg["scale_m"]),
        bestEffort=True, maxPixels=100_000_000, tileScale=4,
    ).get("value_p80").getInfo()
    if percentile is None:
        return {"status": "no_valid_pixels", "dataset": cfg["dataset"], **meta}
    threshold = float(percentile)
    high = valid.And(image.gte(threshold))

    pred_valid = masked_area_km2(ee, valid, predicted, int(cfg["scale_m"]))
    up_valid = masked_area_km2(ee, valid, upwind, int(cfg["scale_m"]))
    intersection = masked_area_km2(ee, high, predicted, int(cfg["scale_m"]))
    high_total = float(
        ee.Image.pixelArea().updateMask(high).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=domain, scale=int(cfg["scale_m"]),
            bestEffort=True, maxPixels=100_000_000, tileScale=4,
        ).get("area").getInfo() or 0.0
    ) / 1_000_000.0

    precision = intersection / pred_valid if pred_valid > 0 else None
    recall = intersection / high_total if high_total > 0 else None
    union_area = pred_valid + high_total - intersection
    csi = intersection / union_area if union_area > 0 else None
    inside = reduce_mean(ee, image, predicted, int(cfg["scale_m"]))
    upwind_mean = reduce_mean(ee, image, upwind, int(cfg["scale_m"]))
    difference = (inside - upwind_mean) if inside is not None and upwind_mean is not None else None
    ratio = (inside / upwind_mean) if inside is not None and upwind_mean is not None and upwind_mean > 0 else None

    centroid = high_centroid(ee, high, domain, int(cfg["scale_m"]))
    obs_bearing = None
    alignment = None
    if centroid is not None:
        obs_bearing = bearing_deg(float(event["lon"]), float(event["lat"]), centroid["lon"], centroid["lat"])
        if model_dir and model_dir.get("bearing_deg") is not None:
            alignment = angular_diff(float(model_dir["bearing_deg"]), obs_bearing)

    predicted_area = local_area_km2(predicted, event)
    return {
        "status": "ok",
        "dataset": cfg["dataset"],
        "band": cfg["band"],
        "image_count": meta.get("image_count"),
        "first_image_time": meta.get("first_image_time"),
        "last_image_time": meta.get("last_image_time"),
        "local_p80_threshold": threshold,
        "predicted_corridor_area_km2": predicted_area,
        "predicted_valid_coverage_fraction": min(1.0, pred_valid / predicted_area) if predicted_area > 0 else None,
        "upwind_valid_area_km2": up_valid,
        "high_signal_area_in_domain_km2": high_total,
        "high_signal_intersection_km2": intersection,
        "precision80": precision,
        "recall80": recall,
        "csi80": csi,
        "downwind_mean": inside,
        "matched_upwind_mean": upwind_mean,
        "downwind_minus_upwind": difference,
        "downwind_to_upwind_ratio": ratio,
        "observed_high_signal_centroid": centroid,
        "observed_high_signal_bearing_deg": obs_bearing,
        "model_observed_bearing_difference_deg": alignment,
    }


def make_geojson(event, perimeter, seeds, tracks, predicted, upwind):
    features = [
        {"type": "Feature", "geometry": mapping(perimeter), "properties": {"kind": "source_perimeter", "event_id": event["id"]}},
        {"type": "Feature", "geometry": mapping(predicted), "properties": {"kind": "downwind_screen", "wind": "IFS 100 m", "buffer_km": 50}},
        {"type": "Feature", "geometry": mapping(upwind), "properties": {"kind": "matched_upwind_control", "construction": "180-degree rotation around reported fire center"}},
    ]
    for i, seed in enumerate(seeds):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [seed["lon"], seed["lat"]]}, "properties": {"kind": "fire_seed", "seed_id": i, "frp_mw": seed.get("frp_mw"), "acq_time": seed.get("acq_time")}})
    for tid, coords in tracks.items():
        features.append({"type": "Feature", "geometry": mapping(LineString(coords)), "properties": {"kind": "trajectory_100m", "seed_id": int(tid)}})
    return {"type": "FeatureCollection", "features": features}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-date", default=None, help="UTC YYYY-MM-DD; default latest date shared by all three satellite products")
    ap.add_argument("--max-events", type=int, default=4)
    ap.add_argument("--min-separation-km", type=float, default=800.0)
    ap.add_argument("--max-seeds", type=int, default=30)
    ap.add_argument("--output-dir", default="artifacts/wildfire_satellite_validation")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ee, project = p.initialize_ee()
    snapshot = p.load_snapshot()
    target_day, latest_days = common_satellite_day(ee, args.target_date)
    events, candidate_count, separation_used = select_events(snapshot, target_day, args.max_events, args.min_separation_km)
    if not events:
        raise RuntimeError(f"No eligible wildfires active on {target_day}")

    results = []
    for event in events:
        item: dict[str, Any] = {
            "event": {
                "id": event.get("id"), "title": event.get("title"), "region": event.get("region"),
                "lat": event.get("lat"), "lon": event.get("lon"), "burned_area_ha": event.get("burned_area_ha"),
                "event_start": event.get("event_start"), "last_detection": event.get("last_detection"),
            },
            "target_date": str(target_day),
        }
        try:
            perimeter, perimeter_source, episode = load_perimeter(event)
            seeds, seed_meta = collect_seeds_for_day(ee, event, perimeter, target_day, args.max_seeds)
            obs, aai_images = observation_time(ee, event, target_day)
            run, creation_ms, base_ms, leads, requested_hours = choose_ifs_run(ee, obs, 12)
            tracks, wind_stats = simulate_100m(ee, run, leads, seeds)
            predicted = p.corridor_geometry(tracks, event, 50.0)
            upwind = mirror_geometry(predicted, event)
            model_dir = model_direction(event, tracks)
            scores = {
                name: score_satellite(ee, name, cfg, event, target_day, obs, predicted, upwind, model_dir)
                for name, cfg in SATELLITES.items()
            }
            positive = [
                name for name, score in scores.items()
                if score.get("status") == "ok"
                and score.get("downwind_minus_upwind") is not None
                and float(score["downwind_minus_upwind"]) > 0
            ]
            aligned = [
                name for name, score in scores.items()
                if score.get("status") == "ok"
                and score.get("model_observed_bearing_difference_deg") is not None
                and float(score["model_observed_bearing_difference_deg"]) <= 60
            ]
            item.update({
                "status": "ok",
                "perimeter_source": perimeter_source,
                "gdacs_episode_id": episode,
                "fire_seeds": {"count": len(seeds), **seed_meta},
                "observation_reference": {"aai_median_overpass_time": p.iso(obs), "aai_intersecting_images": aai_images},
                "ifs": {
                    "dataset": IFS_DATASET,
                    "run_creation_time": p.millis_to_iso(creation_ms),
                    "lead0_valid_time": p.millis_to_iso(base_ms),
                    "observation_offset_from_lead0_hours": requested_hours,
                    "leads_used_hours": leads,
                    "wind_speed_100m_m_s": wind_stats,
                },
                "screen": {
                    "provisional_configuration": "IFS 100 m / 50 km buffer / observation-aligned horizon capped at 12 h",
                    "area_km2": local_area_km2(predicted, event),
                    "model_direction": model_dir,
                    "control": "same-shape corridor rotated 180 degrees about the fire center",
                },
                "satellite_validation": scores,
                "diagnostic_summary": {
                    "positive_downwind_minus_upwind_sensors": positive,
                    "bearing_alignment_le_60deg_sensors": aligned,
                    "note": "Diagnostics are not a calibrated confidence score and are not used to select the events.",
                },
            })
            geo = make_geojson(event, perimeter, seeds, tracks, predicted, upwind)
            (out_dir / f"{event['id']}.geojson").write_text(json.dumps(geo, separators=(",", ":")) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            item.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
        results.append(item)

    summary = {
        "schema_version": "1.0",
        "status": "ok" if any(x.get("status") == "ok" for x in results) else "error",
        "generated_at": p.iso(p.utcnow()),
        "experiment": "wildfire_downwind_satellite_validation_v1",
        "earth_engine_project": project,
        "validation_independence": "Observation-only validation: Sentinel-5P AAI, Sentinel-5P CO and MODIS MAIAC AOD; CAMS/GFAS are not used as truth.",
        "event_selection": {
            "policy": "QC-passed >=10,000 ha wildfires active on target day, sorted by burned area, then greedy geographic separation; satellite outcomes are not used for selection.",
            "eligible_candidate_count": candidate_count,
            "requested_event_count": args.max_events,
            "selected_event_count": len(events),
            "minimum_separation_km_used": separation_used,
        },
        "satellite_latest_days_at_run": latest_days,
        "target_date": str(target_day),
        "target_date_rule": "latest UTC date shared by all three satellite datasets unless overridden",
        "screen_configuration": "provisional V4b modal choice: IFS 100 m wind, 50 km corridor, observation-aligned forecast horizon capped at 12 h",
        "metric_notes": {
            "overlap": "Per-sensor local top-20% signal (>=80th percentile) within 700 km is compared with the predicted corridor only where that sensor has valid coverage.",
            "downwind_enhancement": "Predicted corridor mean is compared with a same-shape 180-degree upwind control.",
            "direction": "Bearing from fire center to local high-signal centroid is compared with bearing to mean model trajectory endpoint.",
            "limitations": [
                "AAI also responds to dust/ash and is not smoke-specific.",
                "CO includes combustion and atmospheric-background sources beyond wildfire.",
                "MAIAC AOD is cloud-sensitive and may have incomplete coverage.",
                "The 80th-percentile plume proxy is a diagnostic threshold, not a validated smoke classification.",
                "This pilot validates transport geometry, not surface PM2.5 dose or health impact.",
            ],
        },
        "events": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
