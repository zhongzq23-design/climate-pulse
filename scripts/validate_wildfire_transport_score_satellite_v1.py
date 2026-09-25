#!/usr/bin/env python3
"""Independent satellite validation for a locked WILDFIRE_TRANSPORT_SCORE_V1 config.

Consumes the frozen 24-fire Satellite Validation V2 artifacts and a config locked
only from the 45-fire CAMS/spatial-block development stage. The satellite
sample is never used to retune sigma, travel-time decay, FRP weighting or the
competing-fire mask.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from shapely.geometry import shape

import probe_wildfire_downwind as p
import probe_wildfire_downwind_satellite_validation as sat1
import probe_wildfire_downwind_satellite_validation_v3 as satv3
import wildfire_transport_score_v1 as core

BOOTSTRAP_N = 5000
SENSORS = ("s5p_aai", "s5p_co", "maiac_aod047")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_one(root: Path, suffix: str) -> Path:
    hits = sorted(x for x in root.rglob(f"*{suffix}") if x.is_file())
    if len(hits) != 1:
        raise RuntimeError(f"Expected exactly one *{suffix} under {root}, found {len(hits)}")
    return hits[0]


def extract_frozen(root: Path):
    summary_path = find_one(root, "summary.json")
    summary = load(summary_path)
    events = [x for x in (summary.get("events") or []) if isinstance(x, dict)]
    if len(events) != 1:
        raise RuntimeError(f"Expected one frozen event in {summary_path}, found {len(events)}")
    item = events[0]
    event = item.get("event") or {}
    event_id = str(event.get("id") or "")
    if not event_id:
        raise RuntimeError("Frozen event id missing")
    geo_path = find_one(root, f"{event_id}.geojson")
    geo = load(geo_path)
    seeds, tracks = [], {}
    perimeter = baseline = None
    for feature in geo.get("features") or []:
        props = feature.get("properties") or {}
        kind = str(props.get("kind") or "")
        geom = feature.get("geometry")
        if not geom:
            continue
        if kind == "source_perimeter":
            perimeter = shape(geom)
        elif kind == "downwind_screen":
            baseline = shape(geom)
        elif kind == "fire_seed":
            coords = geom.get("coordinates") or []
            if len(coords) >= 2:
                seeds.append({"seed_id": int(props.get("seed_id") or 0), "lon": float(coords[0]), "lat": float(coords[1]), "frp_mw": props.get("frp_mw"), "acq_time": props.get("acq_time")})
        elif kind == "trajectory_100m":
            tracks[str(int(props.get("seed_id") or 0))] = [[float(x), float(y)] for x, y in (geom.get("coordinates") or [])]
    seeds.sort(key=lambda x: x["seed_id"])
    if perimeter is None or baseline is None or not seeds or not tracks:
        raise RuntimeError("Frozen artifact missing perimeter/baseline/seeds/tracks")
    return item, event, seeds, tracks, perimeter, baseline, str(summary_path), str(geo_path)


def score_event(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    locked = load(Path(args.locked_json))
    cfg = {"config_id": locked["locked_config_id"], **(locked.get("parameters") or {})}
    try:
        item, event, seeds, tracks, perimeter, baseline, summary_path, geo_path = extract_frozen(Path(args.frozen_dir))
        event_id = str(event["id"])
        ee, project = p.initialize_ee()
        target_day = date.fromisoformat(str(item.get("target_date")))
        obs = core.parse_utc(((item.get("observation_reference") or {}).get("aai_median_overpass_time")))
        ifs = item.get("ifs") or {}
        creation = core.parse_utc(ifs.get("run_creation_time"))
        leads = [int(x) for x in (ifs.get("leads_used_hours") or [])]
        if len(leads) < 2:
            raise RuntimeError(f"Frozen leads unavailable: {leads}")
        run, creation_ms, run_leads = core.ifs_run_for_creation(ee, creation)
        if not all(x in run_leads for x in leads):
            raise RuntimeError(f"Frozen lead sequence no longer available: frozen={leads}, available={run_leads}")
        lookback_days = int(((item.get("fire_seeds") or {}).get("lookback_days")) or 2)
        comp_seeds, comp_meta = core.competitor_points(ee, event, perimeter, obs, lookback_days)
        comp_geom, comp_wind = None, None
        if comp_seeds:
            comp_tracks, comp_wind = sat1.simulate_100m(ee, run, leads, comp_seeds)
            comp_geom = core.competitor_geometry(event, comp_tracks)
        score_geom, geom_meta = core.score_geometry(event, seeds, tracks, leads, baseline, cfg, obs, competitor_wgs=comp_geom)
        score_upwind = satv3.rotate_geometry(score_geom, event, 180.0)
        base_upwind = satv3.rotate_geometry(baseline, event, 180.0)
        baseline_scores, score_scores = {}, {}
        for name, sensor_cfg in sat1.SATELLITES.items():
            baseline_scores[name] = satv3.score_satellite_orientation(ee, name, sensor_cfg, event, target_day, obs, baseline, base_upwind, None)
            score_scores[name] = satv3.score_satellite_orientation(ee, name, sensor_cfg, event, target_day, obs, score_geom, score_upwind, None)
        comparison = {}
        for name in sat1.SATELLITES:
            b, s = baseline_scores[name], score_scores[name]
            bdiff, sdiff = b.get("downwind_minus_control_mean"), s.get("downwind_minus_control_mean")
            comparison[name] = {
                "both_orientation_eligible": bool(b.get("orientation_eligible") and s.get("orientation_eligible")),
                "baseline_directional_contrast": bdiff,
                "transport_score_directional_contrast": sdiff,
                "transport_minus_baseline_contrast": (float(sdiff) - float(bdiff)) if bdiff is not None and sdiff is not None else None,
                "baseline_ratio": b.get("downwind_to_control_mean_ratio"),
                "transport_score_ratio": s.get("downwind_to_control_mean_ratio"),
                "transport_score_positive": bool(sdiff is not None and float(sdiff) > 0),
            }
        doc = {
            "schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1_SATELLITE_VALIDATION", "status": "ok", "event": event,
            "target_date": str(target_day), "frozen_artifacts": {"summary": summary_path, "geojson": geo_path}, "locked_config": locked,
            "score_geometry": geom_meta, "competing_fire": {**comp_meta, "transport_wind_stats": comp_wind},
            "baseline_satellite": baseline_scores, "transport_score_satellite": score_scores, "comparison": comparison,
            "guardrail": "Locked from 45-fire CAMS development before inspecting these satellite outcomes; equal-area comparison; no population/exposure claim."
        }
        dump(out / f"{event_id}.json", doc)
        print(json.dumps({"event_id": event_id, "status": "ok", "comparison": comparison}, indent=2))
    except Exception as exc:  # noqa: BLE001
        event_id = "unknown"
        try:
            event_id = str((extract_frozen(Path(args.frozen_dir))[1] or {}).get("id") or "unknown")
        except Exception:
            pass
        doc = {"schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1_SATELLITE_VALIDATION", "status": "excluded", "event": {"id": event_id}, "reason": f"{type(exc).__name__}: {str(exc)[:1600]}"}
        dump(out / f"{event_id}.json", doc)
        print(json.dumps(doc, indent=2))
    return 0


def haversine(a, b) -> float:
    return p.haversine_km([float(a["lon"]), float(a["lat"])], [float(b["lon"]), float(b["lat"])])


def blocks_complete_link(events: list[dict[str, Any]], threshold_km: float):
    clusters = [[e] for e in sorted(events, key=lambda x: str(x["id"]))]
    while True:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = max(haversine(a, b) for a in clusters[i] for b in clusters[j])
                if d <= threshold_km + 1e-9:
                    ids = "|".join(sorted(str(x["id"]) for x in clusters[i] + clusters[j]))
                    key = (round(d, 9), ids)
                    if best is None or key < best[0]:
                        best = (key, i, j)
        if best is None:
            break
        _, i, j = best
        merged = clusters[i] + clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]
    return [sorted(str(x["id"]) for x in c) for c in clusters]


def qtile(vals: list[float], q: float) -> float:
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return vals[lo] if lo == hi else vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def bootstrap(values: list[float], seed: int):
    if not values:
        return None
    rng = random.Random(seed)
    boots = [statistics.median([values[rng.randrange(len(values))] for _ in values]) for _ in range(BOOTSTRAP_N)]
    return [round(qtile(boots, 0.025), 8), round(qtile(boots, 0.975), 8)]


def aggregate_at_scale(docs: list[dict[str, Any]], threshold_km: float):
    events = [{"id": str((d.get("event") or {}).get("id")), "lat": (d.get("event") or {}).get("lat"), "lon": (d.get("event") or {}).get("lon")} for d in docs if d.get("status") == "ok"]
    blocks = blocks_complete_link(events, threshold_km)
    event_to_block = {eid: f"B{i+1:02d}" for i, ids in enumerate(blocks) for eid in ids}
    out = {"threshold_km": threshold_km, "block_count": len(blocks), "blocks": blocks, "sensors": {}}
    for sensor in SENSORS:
        by_block = defaultdict(list)
        for d in docs:
            if d.get("status") != "ok":
                continue
            eid = str((d.get("event") or {}).get("id") or "")
            comp = (d.get("comparison") or {}).get(sensor) or {}
            delta = comp.get("transport_minus_baseline_contrast")
            if comp.get("both_orientation_eligible") and delta is not None:
                by_block[event_to_block[eid]].append(float(delta))
        block_diffs = [statistics.median(vals) for vals in by_block.values() if vals]
        out["sensors"][sensor] = {
            "eligible_blocks": len(block_diffs), "positive_blocks": sum(x > 0 for x in block_diffs),
            "median_transport_minus_baseline_contrast": round(statistics.median(block_diffs), 8) if block_diffs else None,
            "bootstrap_95ci": bootstrap(block_diffs, 20260916 + int(threshold_km)) if block_diffs else None,
        }
    return out


def summarize(args: argparse.Namespace) -> int:
    docs = [load(x) for x in sorted(Path(args.input_dir).rglob("*.json"))]
    ok = [d for d in docs if d.get("status") == "ok"]
    if not ok:
        raise RuntimeError("No satellite validation events completed")
    sensor_events = {}
    for sensor in SENSORS:
        rows = []
        for d in ok:
            comp = (d.get("comparison") or {}).get(sensor) or {}
            if comp.get("both_orientation_eligible"):
                rows.append({"event_id": str((d.get("event") or {}).get("id")), "baseline_contrast": comp.get("baseline_directional_contrast"), "score_contrast": comp.get("transport_score_directional_contrast"), "improvement": comp.get("transport_minus_baseline_contrast"), "baseline_ratio": comp.get("baseline_ratio"), "score_ratio": comp.get("transport_score_ratio")})
        improvements = [float(x["improvement"]) for x in rows if x.get("improvement") is not None]
        sensor_events[sensor] = {"eligible_events": len(rows), "positive_improvement_events": sum(x > 0 for x in improvements), "median_transport_minus_baseline_contrast": round(statistics.median(improvements), 8) if improvements else None, "event_bootstrap_95ci": bootstrap(improvements, 20260916) if improvements else None, "rows": rows}
    summary = {
        "schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1_SATELLITE_VALIDATION", "status": "experimental_only", "completed_events": len(ok),
        "excluded_events": [{"event_id": str((d.get("event") or {}).get("id")), "reason": d.get("reason")} for d in docs if d.get("status") != "ok"],
        "locked_config": ok[0].get("locked_config"), "event_level": sensor_events,
        "spatial_block_sensitivity": {"500km": aggregate_at_scale(ok, 500.0), "800km": aggregate_at_scale(ok, 800.0)},
        "decision_rule": "Independent confirmation requires the locked score to outperform equal-area V4b directionally without retuning; CO is primary, MAIAC supportive, AAI diagnostic because earlier V2 did not support AAI.",
        "guardrail": "Even a positive result validates directional screening refinement only, not PM2.5 concentration, dose, affected population, health impact, or causal attribution."
    }
    dump(Path(args.output_dir) / "transport_score_satellite_24_summary.json", summary)
    print(json.dumps({"completed_events": len(ok), "event_level": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in sensor_events.items()}, "spatial_block_sensitivity": summary["spatial_block_sensitivity"]}, indent=2))
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__); sub = ap.add_subparsers(dest="mode", required=True)
    q = sub.add_parser("event"); q.add_argument("--frozen-dir", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=score_event)
    q = sub.add_parser("summarize"); q.add_argument("--input-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=summarize)
    return ap


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
