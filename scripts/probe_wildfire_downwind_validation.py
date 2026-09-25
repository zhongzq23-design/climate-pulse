#!/usr/bin/env python3
"""Experimental wildfire downwind validation with IFS + CAMS.

Screening only. This script does NOT estimate smoke concentration, health
impact, or confirmed exposure. It:

1. reuses the fire-specific VIIRS seed matching from the V3 pilot;
2. advects each seed with the latest IFS 100 m, 925 hPa and 850 hPa winds;
3. builds multi-height 30 km screening envelopes at 6/12/24/48 h;
4. intersects those envelopes with the Climate Pulse GHSL population grid;
5. compares the envelopes with CAMS NRT PM2.5 and aerosol optical depth using
   local enrichment and percentile-overlap diagnostics.

CAMS is only a semi-independent validation target because CAMS forecasts use
satellite-derived fire emissions (GFAS). The output therefore measures spatial
agreement with an established atmospheric-composition forecast, not independent
observational truth.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, mapping
from shapely.ops import unary_union

import probe_wildfire_downwind as base
import probe_wildfire_downwind_v2 as v2
import probe_wildfire_downwind_v3 as v3

IFS_DATASET = "ECMWF/NRT_FORECAST/IFS/OPER"
CAMS_DATASET = "ECMWF/CAMS/NRT"
CAMS_PM25 = "particulate_matter_d_less_than_25_um_surface"
CAMS_AOD = "total_aerosol_optical_depth_at_550nm_surface"
CAMS_OM_AOD = "organic_matter_aerosol_optical_depth_at_550nm_surface"
CAMS_BC_AOD = "black_carbon_aerosol_optical_depth_at_550nm_surface"

LEVELS = {
    "100m": ("u_component_of_wind_100m_sfc", "v_component_of_wind_100m_sfc"),
    "925hPa": ("u_component_of_wind_pl925", "v_component_of_wind_pl925"),
    "850hPa": ("u_component_of_wind_pl850", "v_component_of_wind_pl850"),
}
ALL_WIND_BANDS = [band for pair in LEVELS.values() for band in pair]
HORIZONS = (6, 12, 24, 48)
BUFFER_KM = 30.0
CAMS_SCALE_M = 44_528


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def millis_to_dt(ms: int | float) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return float(a) / float(b)


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "max": None}
    return {"median": round(statistics.median(values), 3), "max": round(max(values), 3)}


def sample_winds(ee: Any, image: Any, positions: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    fc = ee.FeatureCollection([
        ee.Feature(
            ee.Geometry.Point([float(p["lon"]), float(p["lat"])]),
            {"track_id": str(p["track_id"])},
        )
        for p in positions
    ])
    sampled = image.select(ALL_WIND_BANDS).sampleRegions(
        collection=fc,
        properties=["track_id"],
        scale=28_000,
        geometries=False,
        tileScale=4,
    )
    out: dict[str, dict[str, float]] = {}
    for feat in (sampled.getInfo() or {}).get("features", []):
        props = feat.get("properties") or {}
        tid = str(props.get("track_id"))
        vals: dict[str, float] = {}
        for band in ALL_WIND_BANDS:
            try:
                if props.get(band) is not None:
                    vals[band] = float(props[band])
            except (TypeError, ValueError):
                pass
        out[tid] = vals
    return out


def simulate_multilevel(ee: Any, run: Any, leads: list[int], seeds: list[dict[str, Any]]):
    tracks = {
        level: {str(i): [[float(s["lon"]), float(s["lat"])]] for i, s in enumerate(seeds)}
        for level in LEVELS
    }
    speeds = {level: [] for level in LEVELS}
    missing = {level: 0 for level in LEVELS}
    requested = {level: 0 for level in LEVELS}

    for idx, lead in enumerate(leads[:-1]):
        next_lead = leads[idx + 1]
        if next_lead > max(HORIZONS):
            break
        dt_seconds = (next_lead - lead) * 3600.0
        subset = run.filter(ee.Filter.eq("forecast_hours", int(lead)))
        if int(subset.size().getInfo() or 0) == 0:
            continue
        image = ee.Image(subset.first())
        positions = []
        for level, table in tracks.items():
            for tid, coords in table.items():
                positions.append({
                    "track_id": f"{level}:{tid}",
                    "lon": coords[-1][0],
                    "lat": coords[-1][1],
                })
        winds = sample_winds(ee, image, positions)

        for level, (uband, vband) in LEVELS.items():
            for tid, coords in tracks[level].items():
                requested[level] += 1
                w = winds.get(f"{level}:{tid}") or {}
                if uband not in w or vband not in w:
                    missing[level] += 1
                    coords.append(list(coords[-1]))
                    continue
                u, v = w[uband], w[vband]
                speeds[level].append(math.hypot(u, v))
                coords.append(list(base.advect(coords[-1][0], coords[-1][1], u, v, dt_seconds)))

    diag = {}
    for level in LEVELS:
        diag[level] = {
            "wind_speed_m_s": stats(speeds[level]),
            "sample_requests": requested[level],
            "missing_samples": missing[level],
            "sample_coverage_fraction": round(1 - missing[level] / requested[level], 4) if requested[level] else None,
        }
    return tracks, diag


def tracks_to_horizon(tracks: dict[str, dict[str, list[list[float]]]], leads: list[int], horizon: int):
    n = sum(1 for x in leads if x <= horizon)
    return {
        level: {tid: coords[:n] for tid, coords in table.items()}
        for level, table in tracks.items()
    }


def multiheight_envelope(event: dict[str, Any], tracks_h: dict[str, dict[str, list[list[float]]]], buffer_km: float):
    fwd, inv = base.local_transformers(float(event["lat"]), float(event["lon"]))
    geoms = []
    for table in tracks_h.values():
        for coords in table.values():
            if len(coords) >= 2:
                g = LineString(coords)
            elif coords:
                g = Point(coords[0])
            else:
                continue
            geoms.append(base.to_local(g, fwd).buffer(float(buffer_km) * 1000.0))
    if not geoms:
        raise RuntimeError("No trajectories available for envelope")
    merged = unary_union(geoms)
    return base.to_wgs84(merged, inv)


def control_and_validation_regions(event: dict[str, Any], envelope):
    fwd, inv = base.local_transformers(float(event["lat"]), float(event["lon"]))
    local = base.to_local(envelope, fwd)
    validation = local.buffer(200_000.0)
    control = validation.difference(local.buffer(50_000.0))
    return base.to_wgs84(control, inv), base.to_wgs84(validation, inv)


def nearest_cams_image(ee: Any, valid_time: datetime):
    col = ee.ImageCollection(CAMS_DATASET)
    for hours in (18, 36, 72):
        subset = col.filterDate(iso(valid_time - timedelta(hours=hours)), iso(valid_time + timedelta(hours=hours)))
        times = [int(x) for x in (subset.aggregate_array("system:time_start").getInfo() or []) if x is not None]
        if times:
            target = int(valid_time.timestamp() * 1000)
            chosen = min(times, key=lambda x: abs(x - target))
            image = ee.Image(subset.filter(ee.Filter.eq("system:time_start", chosen)).first())
            return image, chosen, abs(chosen - target) / 3_600_000.0
    return None, None, None


def region_stat(ee: Any, image: Any, band: str, geom, reducer: Any) -> float | None:
    d = image.select(band).reduceRegion(
        reducer=reducer,
        geometry=ee.Geometry(mapping(geom)),
        scale=CAMS_SCALE_M,
        bestEffort=True,
        maxPixels=20_000_000,
        tileScale=4,
    ).getInfo() or {}
    if not d:
        return None
    value = next(iter(d.values()))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def image_area(ee: Any, mask: Any, geom) -> float | None:
    d = ee.Image.pixelArea().updateMask(mask).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=ee.Geometry(mapping(geom)),
        scale=CAMS_SCALE_M,
        bestEffort=True,
        maxPixels=20_000_000,
        tileScale=4,
    ).getInfo() or {}
    try:
        return float(d.get("area")) if d.get("area") is not None else None
    except (TypeError, ValueError):
        return None


def overlap_metrics(ee: Any, image: Any, band: str, corridor, validation, percentiles=(75, 80, 90)):
    pred_area = image_area(ee, image.select(band).mask(), corridor)
    result: dict[str, Any] = {"predicted_corridor_area_km2": round(pred_area / 1e6, 2) if pred_area else None, "percentiles": {}}
    for q in percentiles:
        threshold = region_stat(ee, image, band, validation, ee.Reducer.percentile([q]))
        if threshold is None:
            result["percentiles"][str(q)] = {"threshold": None}
            continue
        high = image.select(band).gte(threshold)
        high_total = image_area(ee, high, validation)
        intersection = image_area(ee, high, corridor)
        precision = safe_ratio(intersection, pred_area)
        recall = safe_ratio(intersection, high_total)
        union = None
        if pred_area is not None and high_total is not None and intersection is not None:
            union = pred_area + high_total - intersection
        csi = safe_ratio(intersection, union)
        result["percentiles"][str(q)] = {
            "threshold": threshold,
            "precision_corridor_high_fraction": round(precision, 4) if precision is not None else None,
            "recall_local_high_area_captured": round(recall, 4) if recall is not None else None,
            "critical_success_index": round(csi, 4) if csi is not None else None,
            "high_area_total_km2": round(high_total / 1e6, 2) if high_total else None,
            "intersection_km2": round(intersection / 1e6, 2) if intersection else None,
        }
    return result


def cams_validation(ee: Any, image: Any, corridor, control, validation):
    mean = ee.Reducer.mean()
    pm_in = region_stat(ee, image, CAMS_PM25, corridor, mean)
    pm_bg = region_stat(ee, image, CAMS_PM25, control, mean)
    aod_in = region_stat(ee, image, CAMS_AOD, corridor, mean)
    aod_bg = region_stat(ee, image, CAMS_AOD, control, mean)
    om_in = region_stat(ee, image, CAMS_OM_AOD, corridor, mean)
    om_bg = region_stat(ee, image, CAMS_OM_AOD, control, mean)
    bc_in = region_stat(ee, image, CAMS_BC_AOD, corridor, mean)
    bc_bg = region_stat(ee, image, CAMS_BC_AOD, control, mean)

    pm_overlap = overlap_metrics(ee, image, CAMS_PM25, corridor, validation)
    for item in pm_overlap["percentiles"].values():
        if item.get("threshold") is not None:
            item["threshold_ug_m3"] = round(float(item.pop("threshold")) * 1e9, 3)

    return {
        "pm25": {
            "corridor_mean_ug_m3": round(pm_in * 1e9, 3) if pm_in is not None else None,
            "background_mean_ug_m3": round(pm_bg * 1e9, 3) if pm_bg is not None else None,
            "enrichment_ratio": round(safe_ratio(pm_in, pm_bg), 3) if safe_ratio(pm_in, pm_bg) is not None else None,
            "spatial_overlap": pm_overlap,
        },
        "total_aod550": {
            "corridor_mean": round(aod_in, 5) if aod_in is not None else None,
            "background_mean": round(aod_bg, 5) if aod_bg is not None else None,
            "enrichment_ratio": round(safe_ratio(aod_in, aod_bg), 3) if safe_ratio(aod_in, aod_bg) is not None else None,
            "spatial_overlap": overlap_metrics(ee, image, CAMS_AOD, corridor, validation),
        },
        "fire_related_aod_proxy": {
            "definition": "organic_matter_aod550 + black_carbon_aod550; diagnostic proxy only",
            "corridor_mean": round((om_in or 0) + (bc_in or 0), 5) if om_in is not None or bc_in is not None else None,
            "background_mean": round((om_bg or 0) + (bc_bg or 0), 5) if om_bg is not None or bc_bg is not None else None,
            "enrichment_ratio": round(
                safe_ratio((om_in or 0) + (bc_in or 0), (om_bg or 0) + (bc_bg or 0)), 3
            ) if safe_ratio((om_in or 0) + (bc_in or 0), (om_bg or 0) + (bc_bg or 0)) is not None else None,
        },
    }


def geojson_doc(event, perimeter, seeds, tracks, envelopes):
    features = [
        {"type": "Feature", "geometry": mapping(perimeter), "properties": {"kind": "source_perimeter", "event_id": event.get("id")}},
    ]
    for i, seed in enumerate(seeds):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [seed["lon"], seed["lat"]]},
            "properties": {"kind": "fire_seed", "seed_id": i, "sensor": seed.get("sensor"), "acq_time": seed.get("acq_time"), "frp_mw": seed.get("frp_mw")},
        })
    for level, table in tracks.items():
        for tid, coords in table.items():
            if len(coords) >= 2:
                features.append({"type": "Feature", "geometry": mapping(LineString(coords)), "properties": {"kind": "trajectory", "level": level, "seed_id": int(tid)}})
    for horizon, geom in envelopes.items():
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": {"kind": "multiheight_envelope", "horizon_h": int(horizon), "buffer_km": BUFFER_KM}})
    return {"type": "FeatureCollection", "features": features}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event-id", default="gdacs-WF-1031877")
    p.add_argument("--max-seeds", type=int, default=30)
    p.add_argument("--output-dir", default="artifacts/wildfire_validation")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ee, project = base.initialize_ee()
    snap = base.load_snapshot()
    event = base.find_event(snap, args.event_id)
    if str(event.get("type")) != "Wildfire":
        raise RuntimeError("Selected event is not a wildfire")

    perimeter, perimeter_source, episode = v2.load_event_perimeter_stored_first(event)
    seeds, fire_meta = v3.collect_fire_seeds_footprint(
        ee, event, perimeter, now, 120.0, args.max_seeds
    )
    if len(seeds) < 3 or any(bool(x.get("fallback")) for x in seeds):
        raise RuntimeError("Insufficient matched FIRMS/VIIRS fire seeds; validation fails closed")

    run, creation_ms, leads = base.latest_ifs_run(ee, now, max(HORIZONS))
    leads = [x for x in leads if x <= max(HORIZONS)]
    tracks, transport_diag = simulate_multilevel(ee, run, leads, seeds)

    pop_path = base.h.ensure_population_source()
    envelopes = {}
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        if horizon > max(leads):
            horizons[str(horizon)] = {"status": "unavailable", "reason": "IFS lead unavailable"}
            continue
        tracks_h = tracks_to_horizon(tracks, leads, horizon)
        envelope = multiheight_envelope(event, tracks_h, BUFFER_KM)
        envelopes[horizon] = envelope
        pop = base.population(pop_path, envelope)
        control, validation = control_and_validation_regions(event, envelope)
        valid_time = millis_to_dt(creation_ms) + timedelta(hours=horizon)
        cams_image, cams_ms, offset_h = nearest_cams_image(ee, valid_time)
        cams = None
        if cams_image is not None:
            cams = cams_validation(ee, cams_image, envelope, control, validation)
        horizons[str(horizon)] = {
            "status": "ok" if cams is not None else "cams_unavailable",
            "valid_time": iso(valid_time),
            "screening_population": pop,
            "buffer_km": BUFFER_KM,
            "cams_image_time": iso(millis_to_dt(cams_ms)) if cams_ms else None,
            "cams_time_offset_hours": round(offset_h, 2) if offset_h is not None else None,
            "cams": cams,
        }

    cams_col = ee.ImageCollection(CAMS_DATASET)
    cams_latest_ms = cams_col.aggregate_max("system:time_start").getInfo()
    latest_seed = max((base.parse_dt(s.get("acq_time")) for s in seeds if base.parse_dt(s.get("acq_time"))), default=None)

    doc = {
        "schema_version": "1.0",
        "status": "ok",
        "generated_at": iso(now),
        "model_name": "multiheight_wildfire_downwind_cams_validation_v1",
        "interpretation": "Screening envelopes and spatial agreement diagnostics only; not observed smoke exposure or PM2.5 attribution.",
        "event": {
            "id": event.get("id"), "title": event.get("title"), "region": event.get("region"),
            "reported_center": {"lat": event.get("lat"), "lon": event.get("lon")},
            "burned_area_ha": event.get("burned_area_ha"), "last_detection": event.get("last_detection"),
            "perimeter_source": perimeter_source, "gdacs_episode_id": episode,
        },
        "fire_source": {
            "mode": "Earth Engine NASA LANCE VIIRS NRT prototype",
            "note": "Direct FIRMS RT/NRT Area API requires a free FIRMS MAP_KEY; this prototype uses the GEE VIIRS collections already available to the project.",
            "selected_seed_count": len(seeds),
            "latest_selected_detection": iso(latest_seed) if latest_seed else None,
            "latest_selected_detection_age_hours": round((now - latest_seed).total_seconds() / 3600, 2) if latest_seed else None,
            **fire_meta,
        },
        "ifs": {
            "dataset": IFS_DATASET,
            "run_creation_time": iso(millis_to_dt(creation_ms)),
            "run_age_hours": round((now - millis_to_dt(creation_ms)).total_seconds() / 3600, 2),
            "levels": list(LEVELS),
            "lead_hours_used": leads,
            "transport_diagnostics": transport_diag,
        },
        "screening": {
            "horizons_hours": list(HORIZONS),
            "trajectory_buffer_km": BUFFER_KM,
            "envelope_definition": "union of 100 m + 925 hPa + 850 hPa forward-trajectory buffers",
            "population_dataset": "JRC GHSL GHS-WUP-POP R2025A, 2025, ~1 km",
        },
        "cams": {
            "dataset": CAMS_DATASET,
            "latest_collection_time": iso(millis_to_dt(cams_latest_ms)) if cams_latest_ms else None,
            "native_pixel_m": CAMS_SCALE_M,
            "bands": {"pm25": CAMS_PM25, "aod550": CAMS_AOD, "organic_matter_aod550": CAMS_OM_AOD, "black_carbon_aod550": CAMS_BC_AOD},
            "validation_design": "corridor-vs-local-background enrichment plus 75/80/90th-percentile spatial overlap within a 200 km validation neighborhood",
            "independence_caveat": "CAMS uses GFAS satellite-derived fire emissions, so this is semi-independent model-to-model spatial validation rather than independent observation-only validation.",
        },
        "horizons": horizons,
        "limitations": [
            "No plume-rise or injection-height model; pressure levels are transport sensitivities, not diagnosed smoke injection heights.",
            "925 hPa can fall below ground over elevated terrain; level-specific wind sample coverage must be inspected.",
            "The 30 km corridor buffer is a screening sensitivity parameter, not a validated physical plume width.",
            "CAMS PM2.5 and AOD include non-fire aerosol and are themselves forecast/model products.",
            "Direct FIRMS RT/URT was not used because no FIRMS MAP_KEY is configured in this repository.",
        ],
    }

    (out_dir / "validation.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "validation.geojson").write_text(json.dumps(geojson_doc(event, perimeter, seeds, tracks, envelopes), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
