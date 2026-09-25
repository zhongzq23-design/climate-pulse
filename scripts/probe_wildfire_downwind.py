#!/usr/bin/env python3
"""Experimental FIRMS + IFS wildfire downwind population screening.

This is NOT a smoke-concentration model. It combines recent NASA LANCE/FIRMS
VIIRS active-fire detections, the latest ECMWF IFS 10 m/100 m winds, simple
forward advection, fixed trajectory buffers, and the existing Climate Pulse
GHSL population raster. Outputs are potential downwind screening corridors only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform as shp_transform, unary_union

import enrich_hazard_exposure as h

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"

FIRE_DATASETS = (
    ("NASA/LANCE/SNPP_VIIRS/C2", "VIIRS S-NPP 375 m NRT"),
    ("NASA/LANCE/NOAA20_VIIRS/C2", "VIIRS NOAA-20 375 m NRT"),
)
IFS_DATASET = "ECMWF/NRT_FORECAST/IFS/OPER"
WIND_BANDS = (
    "u_component_of_wind_100m_sfc",
    "v_component_of_wind_100m_sfc",
    "u_component_of_wind_10m_sfc",
    "v_component_of_wind_10m_sfc",
)
EE_SCOPES = (
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
)
EARTH_RADIUS_M = 6_371_000.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def millis_to_iso(value: Any) -> str | None:
    try:
        return iso(datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def seconds_to_iso(value: Any) -> str | None:
    try:
        return iso(datetime.fromtimestamp(float(value), tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def initialize_ee():
    import ee
    from google.oauth2 import service_account

    project = os.environ.get("GEE_PROJECT_ID", "").strip()
    raw = os.environ.get("GEE_SERVICE_ACCOUNT_JSON", "").strip()
    if not project or not raw:
        raise RuntimeError("GEE_PROJECT_ID and GEE_SERVICE_ACCOUNT_JSON are required")
    info = json.loads(raw)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=list(EE_SCOPES))
    ee.Initialize(credentials=credentials, project=project)
    return ee, project


def load_snapshot() -> dict[str, Any]:
    return json.loads(LATEST.read_text(encoding="utf-8"))


def find_event(snapshot: dict[str, Any], event_id: str) -> dict[str, Any]:
    for key in ("canonical_events", "events", "source_events"):
        for event in snapshot.get(key) or []:
            if isinstance(event, dict) and str(event.get("id")) == event_id:
                return event
    raise RuntimeError(f"Event not found: {event_id}")


def load_event_perimeter(event: dict[str, Any]):
    source_id = str(event.get("source_id") or "")
    if event.get("origin") == "gdacs" and source_id:
        try:
            episode = h.latest_episode("WF", source_id)
            feats = h.polygon_features("WF", source_id, episode)
            geom = h.polygons_union(feats)
            if geom is not None and not geom.is_empty:
                return geom, "gdacs_gwis_unsimplified", episode
        except Exception as exc:  # noqa: BLE001
            print(f"WARN perimeter fetch: {type(exc).__name__}: {exc}")
    fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
    rel = fp.get("path")
    if rel:
        path = ROOT / str(rel)
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            geom = shape(doc.get("geometry"))
            if not geom.is_empty:
                return geom, "stored_simplified_footprint", doc.get("gdacs_episode_id")
    return Point(float(event["lon"]), float(event["lat"])), "reported_center_fallback", None


def local_transformers(lat: float, lon: float):
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat:.8f} +lon_0={lon:.8f} +datum=WGS84 +units=m +no_defs"
    )
    return (
        Transformer.from_crs("EPSG:4326", local, always_xy=True),
        Transformer.from_crs(local, "EPSG:4326", always_xy=True),
    )


def to_local(geom, transformer: Transformer):
    return shp_transform(transformer.transform, geom)


def to_wgs84(geom, transformer: Transformer):
    return shp_transform(transformer.transform, geom)


def fire_points_for_window(ee: Any, dataset_id: str, label: str, region: Any, start: datetime, end: datetime):
    col = ee.ImageCollection(dataset_id).filterDate(iso(start), iso(end)).filterBounds(region)
    image_count = int(col.size().getInfo() or 0)
    latest_ms = col.aggregate_max("system:time_start").getInfo() if image_count else None
    if not image_count:
        return [], {"dataset": dataset_id, "label": label, "image_count": 0, "latest_image_time": None}

    confidence = col.select("confidence").max().rename("confidence")
    frp = col.select("frp").max().rename("frp")
    acq = col.select("acq_epoch").max().rename("acq_epoch")
    composite = confidence.addBands(frp).addBands(acq).updateMask(confidence.gte(1))
    fc = composite.sample(region=region, scale=375, geometries=True, dropNulls=False, tileScale=2).limit(5000)
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
                "acq_time": seconds_to_iso(acq_epoch),
                "sensor": label,
                "dataset": dataset_id,
            })
        except (TypeError, ValueError):
            continue
    return points, {
        "dataset": dataset_id,
        "label": label,
        "image_count": image_count,
        "latest_image_time": millis_to_iso(latest_ms),
        "sampled_pixels": len(points),
    }


def dedupe_select(points: list[dict[str, Any]], perimeter, event: dict[str, Any], max_seeds: int):
    fwd, _ = local_transformers(float(event["lat"]), float(event["lon"]))
    per_local = to_local(perimeter, fwd).buffer(20_000.0)
    eligible = []
    for p in points:
        x, y = fwd.transform(float(p["lon"]), float(p["lat"]))
        pt = Point(x, y)
        if per_local.contains(pt) or per_local.touches(pt):
            q = dict(p)
            q["_x"], q["_y"] = x, y
            eligible.append(q)
    eligible.sort(
        key=lambda p: (float(p.get("frp_mw") or -1), int(p.get("confidence") or 0), float(p.get("acq_epoch") or 0)),
        reverse=True,
    )
    selected = []
    for p in eligible:
        if any(math.hypot(p["_x"] - q["_x"], p["_y"] - q["_y"]) < 750 for q in selected):
            continue
        selected.append(p)
        if len(selected) >= max_seeds:
            break
    for p in selected:
        p.pop("_x", None)
        p.pop("_y", None)
    return selected, {
        "all_sampled_pixels": len(points),
        "within_perimeter_plus_20km": len(eligible),
        "selected_seed_count": len(selected),
        "selection": "highest FRP, then confidence, then acquisition time; 750 m de-duplication",
    }


def collect_fire_seeds(ee: Any, event: dict[str, Any], perimeter, now: datetime, search_radius_km: float, max_seeds: int):
    region = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(search_radius_km * 1000)
    last_meta = {}
    for days in (2, 4, 7):
        all_points = []
        sensors = []
        for dataset_id, label in FIRE_DATASETS:
            pts, meta = fire_points_for_window(ee, dataset_id, label, region, now - timedelta(days=days), now + timedelta(days=1))
            all_points.extend(pts)
            sensors.append(meta)
        selected, smeta = dedupe_select(all_points, perimeter, event, max_seeds)
        last_meta = {"search_window_days": days, "search_radius_km": search_radius_km, "sensors": sensors, **smeta}
        if len(selected) >= 3:
            return selected, last_meta
    centroid = perimeter.centroid
    last_meta["fallback"] = "No >=3 recent VIIRS detections matched the wildfire perimeter; perimeter centroid used"
    return [{
        "lon": float(centroid.x), "lat": float(centroid.y), "confidence": None, "frp_mw": None,
        "acq_epoch": None, "acq_time": None, "sensor": "none", "dataset": "perimeter-centroid-fallback",
        "fallback": True,
    }], last_meta


def latest_ifs_run(ee: Any, now: datetime, horizon_hours: int):
    col = ee.ImageCollection(IFS_DATASET)
    cutoff = int((now - timedelta(days=14)).timestamp() * 1000)
    recent = col.filter(ee.Filter.gte("creation_time", cutoff))
    creation = recent.aggregate_max("creation_time").getInfo()
    if creation is None:
        creation = col.aggregate_max("creation_time").getInfo()
    if creation is None:
        raise RuntimeError("IFS collection has no run creation_time")
    run = col.filter(ee.Filter.eq("creation_time", creation)).sort("forecast_hours")
    available = sorted({int(x) for x in (run.aggregate_array("forecast_hours").getInfo() or [])})
    leads = [x for x in available if 0 <= x <= int(horizon_hours)]
    if len(leads) < 2:
        raise RuntimeError(f"Insufficient IFS leads within {horizon_hours} h: {leads}")
    return run, int(creation), leads


def sample_wind(ee: Any, image: Any, positions: list[dict[str, Any]]):
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([float(p["lon"]), float(p["lat"])]), {"track_id": str(p["track_id"])})
        for p in positions
    ])
    sampled = image.select(list(WIND_BANDS)).sampleRegions(
        collection=fc, properties=["track_id"], scale=28000, geometries=False, tileScale=2
    )
    out = {}
    for feat in (sampled.getInfo() or {}).get("features", []):
        props = feat.get("properties") or {}
        tid = str(props.get("track_id"))
        try:
            out[tid] = {band: float(props[band]) for band in WIND_BANDS}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def advect(lon: float, lat: float, u: float, v: float, dt_seconds: float):
    dlat = (v * dt_seconds / EARTH_RADIUS_M) * (180.0 / math.pi)
    cos_lat = max(0.05, abs(math.cos(math.radians(lat))))
    dlon = (u * dt_seconds / (EARTH_RADIUS_M * cos_lat)) * (180.0 / math.pi)
    lon2 = lon + dlon
    if lon2 > 180:
        lon2 -= 360
    if lon2 < -180:
        lon2 += 360
    return lon2, max(-89.9, min(89.9, lat + dlat))


def haversine_km(a: list[float], b: list[float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(x)))


def stats(values: list[float]):
    return {"median": round(statistics.median(values), 3), "max": round(max(values), 3)} if values else {"median": None, "max": None}


def simulate_tracks(ee: Any, run: Any, leads: list[int], seeds: list[dict[str, Any]]):
    tracks100 = {str(i): [[float(s["lon"]), float(s["lat"])]] for i, s in enumerate(seeds)}
    tracks10 = {str(i): [[float(s["lon"]), float(s["lat"])]] for i, s in enumerate(seeds)}
    speed100, speed10 = [], []
    for idx, lead in enumerate(leads[:-1]):
        next_lead = leads[idx + 1]
        dt = (next_lead - lead) * 3600.0
        subset = run.filter(ee.Filter.eq("forecast_hours", int(lead)))
        if int(subset.size().getInfo() or 0) == 0:
            continue
        image = ee.Image(subset.first())
        positions = []
        for tid, coords in tracks100.items():
            positions.append({"track_id": f"100:{tid}", "lon": coords[-1][0], "lat": coords[-1][1]})
        for tid, coords in tracks10.items():
            positions.append({"track_id": f"10:{tid}", "lon": coords[-1][0], "lat": coords[-1][1]})
        winds = sample_wind(ee, image, positions)
        for tid, coords in tracks100.items():
            w = winds.get(f"100:{tid}")
            if not w:
                coords.append(list(coords[-1])); continue
            u, v = w["u_component_of_wind_100m_sfc"], w["v_component_of_wind_100m_sfc"]
            speed100.append(math.hypot(u, v))
            coords.append(list(advect(coords[-1][0], coords[-1][1], u, v, dt)))
        for tid, coords in tracks10.items():
            w = winds.get(f"10:{tid}")
            if not w:
                coords.append(list(coords[-1])); continue
            u, v = w["u_component_of_wind_10m_sfc"], w["v_component_of_wind_10m_sfc"]
            speed10.append(math.hypot(u, v))
            coords.append(list(advect(coords[-1][0], coords[-1][1], u, v, dt)))
    divergence = [haversine_km(tracks100[k][-1], tracks10[k][-1]) for k in tracks100]
    return tracks100, tracks10, {
        "wind_speed_100m_m_s": stats(speed100),
        "wind_speed_10m_m_s": stats(speed10),
        "final_10m_100m_track_divergence_km": stats(divergence),
    }


def corridor_geometry(tracks: dict[str, list[list[float]]], event: dict[str, Any], buffer_km: float):
    fwd, inv = local_transformers(float(event["lat"]), float(event["lon"]))
    lines = [LineString(coords) for coords in tracks.values() if len(coords) >= 2]
    merged = unary_union([to_local(line, fwd) for line in lines])
    return to_wgs84(merged.buffer(float(buffer_km) * 1000.0), inv)


def population(pop_path: Path, geom) -> int:
    import rasterio
    with rasterio.open(pop_path) as src:
        return h.raster_population(src, geom)


def make_geojson(event, perimeter, seeds, tracks100, tracks10, corridor30, envelope30):
    features = [
        {"type": "Feature", "geometry": mapping(perimeter), "properties": {"kind": "source_perimeter", "event_id": event.get("id")}},
        {"type": "Feature", "geometry": mapping(corridor30), "properties": {"kind": "corridor_30km_100m", "interpretation": "screening only"}},
        {"type": "Feature", "geometry": mapping(envelope30), "properties": {"kind": "corridor_30km_10m_100m_envelope", "interpretation": "sensitivity envelope"}},
    ]
    for i, seed in enumerate(seeds):
        features.append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [seed["lon"], seed["lat"]]},
            "properties": {"kind": "fire_seed", "seed_id": i, "sensor": seed.get("sensor"), "frp_mw": seed.get("frp_mw"), "confidence": seed.get("confidence"), "acq_time": seed.get("acq_time"), "fallback": bool(seed.get("fallback"))},
        })
    for tid, coords in tracks100.items():
        features.append({"type": "Feature", "geometry": mapping(LineString(coords)), "properties": {"kind": "trajectory_100m", "seed_id": int(tid)}})
    for tid, coords in tracks10.items():
        features.append({"type": "Feature", "geometry": mapping(LineString(coords)), "properties": {"kind": "trajectory_10m_sensitivity", "seed_id": int(tid)}})
    return {"type": "FeatureCollection", "features": features}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event-id", default="gdacs-WF-1031776")
    p.add_argument("--horizon-hours", type=int, default=24)
    p.add_argument("--search-radius-km", type=float, default=120.0)
    p.add_argument("--max-seeds", type=int, default=30)
    p.add_argument("--output-dir", default="artifacts/wildfire_downwind")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    ee, project = initialize_ee()
    event = find_event(load_snapshot(), args.event_id)
    if str(event.get("type")) != "Wildfire":
        raise RuntimeError(f"Event is not wildfire: {args.event_id}")

    perimeter, perimeter_source, episode = load_event_perimeter(event)
    seeds, fire_meta = collect_fire_seeds(ee, event, perimeter, now, args.search_radius_km, args.max_seeds)
    latest_acq = max((parse_dt(s.get("acq_time")) for s in seeds if s.get("acq_time")), default=None)

    run, creation_ms, leads = latest_ifs_run(ee, now, args.horizon_hours)
    tracks100, tracks10, wind_meta = simulate_tracks(ee, run, leads, seeds)
    c10 = corridor_geometry(tracks100, event, 10)
    c30 = corridor_geometry(tracks100, event, 30)
    c50 = corridor_geometry(tracks100, event, 50)
    c30_10m = corridor_geometry(tracks10, event, 30)
    envelope30 = unary_union([c30, c30_10m])

    pop_path = h.ensure_population_source()
    pops = {
        "trajectory_corridor_10km": population(pop_path, c10),
        "trajectory_corridor_30km": population(pop_path, c30),
        "trajectory_corridor_50km": population(pop_path, c50),
        "wind_height_sensitivity_envelope_30km": population(pop_path, envelope30),
    }

    result = {
        "schema_version": "1.0",
        "status": "ok",
        "generated_at": iso(now),
        "model_name": "wind_advected_wildfire_screening_corridor_v1",
        "interpretation": "Potential downwind resident-population screening corridor only; not observed smoke, PM2.5 concentration, affected population, health impact, or causal attribution.",
        "event": {
            "id": event.get("id"), "title": event.get("title"), "region": event.get("region"),
            "reported_center": {"lat": event.get("lat"), "lon": event.get("lon")},
            "burned_area_ha": event.get("burned_area_ha"), "last_detection": event.get("last_detection"),
            "perimeter_source": perimeter_source, "gdacs_episode_id": episode,
        },
        "fire_source": {
            "datasets": [x[0] for x in FIRE_DATASETS], "selected_seed_count": len(seeds),
            "latest_selected_detection": iso(latest_acq) if latest_acq else None,
            "latest_selected_detection_age_hours": round((now - latest_acq).total_seconds() / 3600, 2) if latest_acq else None,
            **fire_meta,
        },
        "meteorology": {
            "dataset": IFS_DATASET, "project": project, "run_creation_time": millis_to_iso(creation_ms),
            "run_age_hours": round((now.timestamp() * 1000 - creation_ms) / 3_600_000, 2),
            "forecast_leads_hours_used": leads, "horizon_hours": max(leads),
            "primary_transport_wind": "100 m IFS horizontal wind", "sensitivity_transport_wind": "10 m IFS horizontal wind",
            **wind_meta,
        },
        "corridor_definition": {
            "primary_buffer_km": 30, "sensitivity_buffers_km": [10, 30, 50],
            "rationale": "Fixed buffers around forward trajectories are a screening device. The 30 km default follows published fire-specific trajectory-window sensitivity work that found 10 km too restrictive and used 30 km as a conservative compromise.",
        },
        "population": {
            "dataset": "JRC GHSL GHS-WUP-POP R2025A, 2025, ~1 km",
            "metric": "unique resident population intersecting the unioned screening corridor",
            **pops,
        },
        "limitations": [
            "No emissions rate or smoke PM2.5 concentration is estimated.",
            "No plume-rise or injection-height model is included.",
            "Transport uses 2-D 10 m and 100 m winds rather than full 3-D meteorology.",
            "No boundary-layer turbulence, wet/dry deposition, chemistry, topographic channeling, or rainout is simulated.",
            "FIRMS/VIIRS NRT detections are not science-quality final fire products.",
            "Population counts are residents in a screening corridor, not people confirmed to inhale smoke.",
        ],
    }

    geojson = make_geojson(event, perimeter, seeds, tracks100, tracks10, c30, envelope30)
    (out_dir / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "footprint.geojson").write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
