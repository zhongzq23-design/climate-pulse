#!/usr/bin/env python3
"""WILDFIRE_TRANSPORT_SCORE_V1 experimental benchmark.

Research prototype only; never imported by production/publication paths.

The experiment keeps the V4b screening family fixed at IFS 100 m / <=12 h and
compares the original 50 km binary corridor with equal-area top-score regions
constructed from a continuous transport score. Candidate score variants test:

* cross-trajectory Gaussian distance decay;
* forward travel-time decay;
* optional sqrt(FRP) source weighting;
* optional masking of areas intersected by trajectories from nearby non-focal
  active-fire detections.

Model development uses the frozen 45-fire V4 spatial-block universe and the
same block/embargo geometry. Independent satellite validation is handled by a
separate script after one score configuration is locked.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from shapely import contains_xy
from shapely.affinity import scale as shp_scale
from shapely.geometry import Point, box
from shapely.ops import unary_union

import benchmark_wildfire_downwind as v1
import benchmark_wildfire_downwind_v3 as v3
import benchmark_wildfire_downwind_v4 as v4
import probe_wildfire_downwind as p
import probe_wildfire_downwind_v2 as pv2
import probe_wildfire_downwind_v3 as pv3
import probe_wildfire_downwind_satellite_validation as sat

IFS_DATASET = p.IFS_DATASET
GRID_KM = 5.0
DOMAIN_PAD_KM = 80.0
COMPETITOR_SEARCH_KM = 500.0
COMPETITOR_MAX_SEEDS = 30
COMPETITOR_BUFFER_KM = 50.0
SEED_RECENCY_TAU_H = 36.0
SIGMAS_KM = (20.0, 35.0)
TRAVEL_TAUS_H = (6.0, 12.0)
FRP_OPTIONS = (False, True)
COMPETITOR_OPTIONS = (False, True)
MIN_BLOCK_SUPPORT_FRACTION = 0.90
BOOTSTRAP_N = 5000
METRICS = ("pm25_enrichment_ratio", "precision80", "recall80", "csi80")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def github_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def median(values) -> float | None:
    vals = [x for x in (finite(v) for v in values) if x is not None]
    return statistics.median(vals) if vals else None


def quantile(values, q: float) -> float | None:
    vals = sorted(x for x in (finite(v) for v in values) if x is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def dist(values) -> dict[str, Any]:
    vals = [x for x in (finite(v) for v in values) if x is not None]
    return {
        "n": len(vals),
        "p10": round(quantile(vals, 0.10), 5) if vals else None,
        "median": round(quantile(vals, 0.50), 5) if vals else None,
        "p90": round(quantile(vals, 0.90), 5) if vals else None,
    }


def variant_id(sigma_km: float, tau_h: float, frp: bool, competitors: bool) -> str:
    return f"score|sigma{int(sigma_km)}|tau{int(tau_h)}|frp{int(frp)}|comp{int(competitors)}"


def variant_grid() -> list[dict[str, Any]]:
    return [
        {
            "config_id": variant_id(sigma, tau, frp, comp),
            "sigma_km": float(sigma),
            "travel_tau_h": float(tau),
            "frp_weighting": bool(frp),
            "competitor_masking": bool(comp),
        }
        for sigma in SIGMAS_KM
        for tau in TRAVEL_TAUS_H
        for frp in FRP_OPTIONS
        for comp in COMPETITOR_OPTIONS
    ]


def discover(args: argparse.Namespace) -> int:
    source = load(Path(args.discovery_json))
    events = source.get("events") or []
    if len(events) != 45:
        raise RuntimeError(f"Expected frozen V4 universe of 45 events, found {len(events)}")
    doc = {
        "schema_version": "1.0",
        "experiment": "WILDFIRE_TRANSPORT_SCORE_V1",
        "source_v4_generated_at": source.get("generated_at"),
        "event_count": len(events),
        "spatial_blocks": source.get("spatial_blocks"),
        "events": events,
        "variant_grid": variant_grid(),
        "guardrail": "Reuse frozen 45-fire V4 spatial universe; never tune on the independent satellite outcomes; geodesic-area match every score geometry to the event-specific 100 m / 50 km / 12 h baseline.",
    }
    dump(Path(args.output_dir) / "transport_score_discovery.json", doc)
    github_output("event_matrix", json.dumps([{"event_id": str(x["event_id"])} for x in events], separators=(",", ":")))
    print(json.dumps({"event_count": len(events), "variant_count": len(doc["variant_grid"])}, indent=2))
    return 0


def find_json(folder: Path, event_id: str | None = None) -> Path:
    hits = sorted(folder.rglob("*.json"))
    if event_id:
        exact = [x for x in hits if x.name == f"{event_id}.json"]
        if len(exact) == 1:
            return exact[0]
    if len(hits) != 1:
        raise RuntimeError(f"Expected one JSON under {folder}, found {len(hits)}")
    return hits[0]


def ifs_run_for_creation(ee: Any, creation: datetime):
    creation_ms = int(round(creation.timestamp() * 1000))
    col = ee.ImageCollection(IFS_DATASET).filter(ee.Filter.eq("creation_time", creation_ms)).sort("forecast_hours")
    available = sorted({int(x) for x in (col.aggregate_array("forecast_hours").getInfo() or [])})
    leads = [x for x in available if 0 <= x <= 12]
    if len(leads) < 2:
        all_col = ee.ImageCollection(IFS_DATASET).filterDate(p.iso(creation - timedelta(hours=1)), p.iso(creation + timedelta(hours=1)))
        creations = sorted({int(x) for x in (all_col.aggregate_array("creation_time").getInfo() or []) if x is not None})
        if not creations:
            raise RuntimeError(f"Frozen IFS cycle unavailable around {p.iso(creation)}")
        chosen = min(creations, key=lambda x: abs(x - creation_ms))
        col = ee.ImageCollection(IFS_DATASET).filter(ee.Filter.eq("creation_time", chosen)).sort("forecast_hours")
        available = sorted({int(x) for x in (col.aggregate_array("forecast_hours").getInfo() or [])})
        leads = [x for x in available if 0 <= x <= 12]
        creation_ms = chosen
    if len(leads) < 2:
        raise RuntimeError(f"Insufficient frozen IFS leads for 12 h: {leads}")
    return col, creation_ms, leads


def _dedupe_points(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]):
        return (-(finite(row.get("frp_mw")) or 0.0), -(finite(row.get("acq_epoch")) or 0.0), row.get("lat"), row.get("lon"))
    selected: list[dict[str, Any]] = []
    for row in sorted(points, key=key):
        here = [float(row["lon"]), float(row["lat"])]
        if any(p.haversine_km(here, [float(x["lon"]), float(x["lat"])]) < 5.0 for x in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def competitor_points(ee: Any, event: dict[str, Any], perimeter, reference_time: datetime, search_days: int):
    region = ee.Geometry.Point([float(event["lon"]), float(event["lat"])]).buffer(COMPETITOR_SEARCH_KM * 1000)
    raw: list[dict[str, Any]] = []
    metas = []
    for dataset_id, label in p.FIRE_DATASETS:
        pts, meta = pv2.fire_points_for_window_fixed(ee, dataset_id, label, region, reference_time - timedelta(days=search_days), reference_time + timedelta(days=1))
        raw.extend(pts)
        metas.append(meta)
    fwd, _ = p.local_transformers(float(event["lat"]), float(event["lon"]))
    focal_local = p.to_local(perimeter, fwd).buffer(20_000.0)
    nonfocal = []
    for row in raw:
        lp = Point(*fwd.transform(float(row["lon"]), float(row["lat"])))
        if focal_local.covers(lp):
            continue
        if p.haversine_km([float(event["lon"]), float(event["lat"])], [float(row["lon"]), float(row["lat"])]) > COMPETITOR_SEARCH_KM:
            continue
        nonfocal.append(row)
    selected = _dedupe_points(nonfocal, COMPETITOR_MAX_SEEDS)
    return selected, {
        "search_radius_km": COMPETITOR_SEARCH_KM,
        "search_window_days": int(search_days),
        "raw_nonfocal_detection_count": len(nonfocal),
        "selected_competitor_seed_count": len(selected),
        "sensors": metas,
    }


def competitor_geometry(event: dict[str, Any], tracks: dict[str, list[list[float]]]):
    return p.corridor_geometry(tracks, event, COMPETITOR_BUFFER_KM) if tracks else None


def _local_track_nodes(event: dict[str, Any], seeds, tracks, leads):
    fwd, _ = p.local_transformers(float(event["lat"]), float(event["lon"]))
    frps = [max(0.0, finite(x.get("frp_mw")) or 0.0) for x in seeds]
    positive = [x for x in frps if x > 0]
    frp_median = statistics.median(positive) if positive else 1.0
    nodes = []
    for idx, seed in enumerate(seeds):
        coords = tracks.get(str(idx)) or []
        frp = max(0.0, finite(seed.get("frp_mw")) or 0.0)
        acq = p.parse_dt(seed.get("acq_time"))
        for j, coord in enumerate(coords):
            if j >= len(leads):
                break
            x, y = fwd.transform(float(coord[0]), float(coord[1]))
            nodes.append({
                "seed_id": idx,
                "x": float(x), "y": float(y), "lead_h": float(leads[j]),
                "frp_relative": max(0.05, frp / max(frp_median, 1e-6)) if frp > 0 else 0.25,
                "acq": acq,
            })
    return nodes


def _grid(baseline_local):
    pad = DOMAIN_PAD_KM * 1000.0
    minx, miny, maxx, maxy = baseline_local.buffer(pad).bounds
    step = GRID_KM * 1000.0
    xs = np.arange(math.floor(minx / step) * step + step / 2, maxx, step)
    ys = np.arange(math.floor(miny / step) * step + step / 2, maxy, step)
    xx, yy = np.meshgrid(xs, ys)
    return xx.ravel(), yy.ravel(), step


def score_geometry(event, seeds, tracks, leads, baseline_wgs, cfg, reference_time, competitor_wgs=None):
    fwd, inv = p.local_transformers(float(event["lat"]), float(event["lon"]))
    baseline_local = p.to_local(baseline_wgs, fwd)
    xs, ys, step = _grid(baseline_local)
    nodes = _local_track_nodes(event, seeds, tracks, leads)
    if not nodes:
        raise RuntimeError("No focal trajectory nodes available for transport score")
    sigma = float(cfg["sigma_km"]) * 1000.0
    tau = float(cfg["travel_tau_h"])
    score = np.zeros(xs.shape, dtype="float64")
    for node in nodes:
        acq = node["acq"]
        age_h = max(0.0, (reference_time - acq).total_seconds() / 3600.0) if acq is not None else 24.0
        recency = math.exp(-age_h / SEED_RECENCY_TAU_H)
        frp_weight = math.sqrt(max(0.05, node["frp_relative"])) if cfg.get("frp_weighting") else 1.0
        time_weight = math.exp(-float(node["lead_h"]) / max(tau, 1e-6))
        d2 = (xs - float(node["x"])) ** 2 + (ys - float(node["y"])) ** 2
        score += frp_weight * recency * time_weight * np.exp(-d2 / (2.0 * sigma * sigma))
    competitor_masked = np.zeros(xs.shape, dtype=bool)
    if cfg.get("competitor_masking") and competitor_wgs is not None and not competitor_wgs.is_empty:
        comp_local = p.to_local(competitor_wgs, fwd)
        competitor_masked = contains_xy(comp_local, xs, ys)
        score[competitor_masked] = -np.inf
    target_area_m2 = float(baseline_local.area)
    cell_area = step * step
    n_cells = min(max(1, int(round(target_area_m2 / cell_area))), len(score))
    order = np.argsort(score, kind="stable")[::-1]
    chosen = order[np.isfinite(score[order])][:n_cells]
    if len(chosen) < max(1, int(0.8 * n_cells)):
        raise RuntimeError("Competing-fire mask leaves insufficient finite score cells")
    half = step / 2.0
    selected_local = unary_union([box(float(xs[i] - half), float(ys[i] - half), float(xs[i] + half), float(ys[i] + half)) for i in chosen])
    if selected_local.is_empty:
        raise RuntimeError("Transport-score top-area geometry is empty")
    selected_local = shp_scale(selected_local, xfact=math.sqrt(target_area_m2 / max(selected_local.area, 1e-9)), yfact=math.sqrt(target_area_m2 / max(selected_local.area, 1e-9)), origin=(0.0, 0.0))
    target_geod = v4.geodesic_area_km2(baseline_wgs)
    for _ in range(6):
        selected_wgs = p.to_wgs84(selected_local, inv)
        current = v4.geodesic_area_km2(selected_wgs)
        if current <= 0:
            break
        ratio = target_geod / current
        if abs(ratio - 1.0) < 1e-7:
            break
        factor = math.sqrt(ratio)
        selected_local = shp_scale(selected_local, xfact=factor, yfact=factor, origin=(0.0, 0.0))
    selected_wgs = p.to_wgs84(selected_local, inv)
    selected_geod = v4.geodesic_area_km2(selected_wgs)
    return selected_wgs, {
        "grid_km": GRID_KM,
        "candidate_cell_count": int(len(score)),
        "selected_cell_count": int(len(chosen)),
        "score_min_selected": float(np.min(score[chosen])) if len(chosen) else None,
        "score_median_selected": float(np.median(score[chosen])) if len(chosen) else None,
        "competitor_masked_cell_fraction": round(float(np.mean(competitor_masked)), 6),
        "baseline_geodesic_area_km2": round(target_geod, 6),
        "selected_geodesic_area_km2": round(selected_geod, 6),
        "area_match_error_fraction": round(abs(selected_geod - target_geod) / target_geod, 9) if target_geod else None,
    }


def score_event(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    event_id = str(args.event_id)
    try:
        source_doc = load(find_json(Path(args.v4_event_dir), event_id))
        frozen_creation = parse_utc(((source_doc.get("ifs") or {}).get("run_creation_time")))
        ee, project = p.initialize_ee()
        event = p.find_event(p.load_snapshot(), event_id)
        perimeter, perimeter_source, episode = pv2.load_event_perimeter_stored_first(event)
        seeds, fire_meta = pv3.collect_fire_seeds_footprint(ee, event, perimeter, frozen_creation, 120.0, 30)
        run, creation_ms, leads = ifs_run_for_creation(ee, frozen_creation)
        tracks, wind_stats = sat.simulate_100m(ee, run, leads, seeds)
        baseline = p.corridor_geometry(tracks, event, 50.0)
        search_days = int((fire_meta or {}).get("search_window_days") or 2)
        comp_seeds, comp_meta = competitor_points(ee, event, perimeter, frozen_creation, search_days)
        comp_tracks, comp_wind_stats, comp_geom = {}, None, None
        if comp_seeds:
            comp_tracks, comp_wind_stats = sat.simulate_100m(ee, run, leads, comp_seeds)
            comp_geom = competitor_geometry(event, comp_tracks)
        valid_time = v1.val.millis_to_dt(creation_ms) + timedelta(hours=12)
        cams_image, cams_ms, offset = v1.val.nearest_cams_image(ee, valid_time)
        if cams_image is None:
            raise RuntimeError("Frozen CAMS validation image unavailable")
        control, validation = v1.fixed_regions(event, baseline)
        configs = [("baseline_v4b", baseline, {"family": "baseline_v4b", "height_mode": "100m", "radius_km": 50, "horizon_h": 12, "geometric_area_km2": round(v4.geodesic_area_km2(baseline), 6)})]
        for cfg in variant_grid():
            geom, meta = score_geometry(event, seeds, tracks, leads, baseline, cfg, frozen_creation, competitor_wgs=comp_geom)
            configs.append((cfg["config_id"], geom, {"family": "transport_score", "height_mode": "100m", "horizon_h": 12, "geometric_area_km2": round(v4.geodesic_area_km2(geom), 6), **cfg, **meta}))
        rows = v1.batch_primary_metrics(ee, cams_image, configs, control, validation)
        for row in rows:
            row["cams_time_offset_hours"] = offset
        doc = {
            "schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1", "status": "ok", "generated_at": v1.now_iso(),
            "event": {"id": event_id, "lat": event.get("lat"), "lon": event.get("lon"), "last_detection": event.get("last_detection"), "burned_area_ha": event.get("burned_area_ha"), "perimeter_source": perimeter_source, "episode": episode},
            "frozen_v4_reference": {"source_run_creation_time": p.iso(frozen_creation), "source_config": "100 m / 50 km / 12 h", "source_v4_event_status": source_doc.get("status")},
            "focal_fire": {**(fire_meta or {}), "selected_seed_count": len(seeds)},
            "competing_fire": {**comp_meta, "corridor_buffer_km": COMPETITOR_BUFFER_KM, "transport_wind_stats": comp_wind_stats},
            "ifs": {"dataset": IFS_DATASET, "run_creation_time": p.millis_to_iso(creation_ms), "leads_used_hours": leads, "focal_wind_speed_100m_m_s": wind_stats},
            "cams": {"valid_time": p.iso(valid_time), "image_time": p.millis_to_iso(cams_ms), "time_offset_hours": offset},
            "score_definition": {"formula": "sum_seed,lead source_weight * exp(-travel_h/tau) * exp(-distance^2/(2*sigma^2))", "source_weight": "exp(-seed_age/36h) times optional sqrt(FRP/event-median-FRP)", "competitor_mask": "optional removal of cells intersecting 50 km corridors from up to 30 nearby non-focal VIIRS seeds", "equal_area": "top-score grid region iteratively matched to baseline geodesic area"},
            "rows": rows,
        }
        dump(out / f"{event_id}.json", doc)
        print(json.dumps({"event_id": event_id, "status": "ok", "rows": len(rows), "competitor_seeds": len(comp_seeds)}, indent=2))
    except Exception as exc:  # noqa: BLE001
        doc = {"schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1", "status": "excluded", "generated_at": v1.now_iso(), "event": {"id": event_id}, "reason": f"{type(exc).__name__}: {str(exc)[:1600]}"}
        dump(out / f"{event_id}.json", doc)
        print(json.dumps(doc, indent=2))
    return 0


def row_for(doc: dict[str, Any], config_id: str) -> dict[str, Any] | None:
    return next((row for row in (doc.get("rows") or []) if str(row.get("config_id")) == config_id), None)


def block_config_summary(docs, event_to_block, allowed_ids: set[str] | None = None):
    by: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    first_rows = {}
    for doc in docs:
        if doc.get("status") != "ok":
            continue
        eid = str((doc.get("event") or {}).get("id") or "")
        if allowed_ids is not None and eid not in allowed_ids:
            continue
        bid = event_to_block.get(eid)
        if not bid:
            continue
        for row in doc.get("rows") or []:
            if row.get("family") != "transport_score":
                continue
            cid = str(row["config_id"])
            by[cid][bid].append(row)
            first_rows.setdefault(cid, row)
    out = []
    for cid, blocks in by.items():
        block_values = [{"spatial_block_id": bid, "n": len(rows), **{metric: median([r.get(metric) for r in rows]) for metric in METRICS}} for bid, rows in sorted(blocks.items())]
        first = first_rows[cid]
        out.append({"config_id": cid, "blocks_n": len(block_values), "events_n": sum(x["n"] for x in block_values), "sigma_km": first.get("sigma_km"), "travel_tau_h": first.get("travel_tau_h"), "frp_weighting": first.get("frp_weighting"), "competitor_masking": first.get("competitor_masking"), **{metric: dist([x.get(metric) for x in block_values]) for metric in METRICS}, "block_values": block_values})
    return out


def select_config(docs, event_to_block, allowed_ids: set[str]):
    summaries = block_config_summary(docs, event_to_block, allowed_ids)
    if not summaries:
        raise RuntimeError("No transport-score configs available for selection")
    support_floor = max(1, math.ceil(max(x["blocks_n"] for x in summaries) * MIN_BLOCK_SUPPORT_FRACTION))
    eligible = [x for x in summaries if x["blocks_n"] >= support_floor and finite((x.get("csi80") or {}).get("median")) is not None]
    if not eligible:
        raise RuntimeError("No sufficiently supported transport-score configs")
    eligible.sort(key=lambda x: (-float(x["csi80"]["median"]), -float((x.get("precision80") or {}).get("median") or -1), -float((x.get("pm25_enrichment_ratio") or {}).get("median") or -1), str(x["config_id"])))
    return eligible[0], {"selection_unit": "training spatial block", "support_floor_blocks": support_floor, "eligible_config_count": len(eligible), "primary_metric": "median training-block CSI80; precision80 then PM2.5 enrichment as tie-breakers"}


def bootstrap_block_differences(folds, metric: str):
    diffs = []
    for fold in folds:
        a = finite((fold.get("transport_score") or {}).get(metric)); b = finite((fold.get("baseline_v4b") or {}).get(metric))
        if a is not None and b is not None:
            diffs.append(a - b)
    if not diffs:
        return {"n_blocks": 0, "median_difference": None, "bootstrap_95ci": None}
    rng = random.Random(20260916)
    boots = [statistics.median([diffs[rng.randrange(len(diffs))] for _ in diffs]) for _ in range(BOOTSTRAP_N)]
    return {"n_blocks": len(diffs), "median_difference": round(statistics.median(diffs), 5), "bootstrap_95ci": [round(quantile(boots, 0.025), 5), round(quantile(boots, 0.975), 5)], "positive_blocks": sum(x > 0 for x in diffs)}


def summarize45(args: argparse.Namespace) -> int:
    source = load(Path(args.discovery_json))
    docs = [load(x) for x in sorted(Path(args.input_dir).rglob("*.json"))]
    by_doc = {str((d.get("event") or {}).get("id")): d for d in docs if (d.get("event") or {}).get("id")}
    events_meta = {str(x["event_id"]): x for x in source.get("events") or []}
    event_to_block = {eid: str(meta["spatial_block_id"]) for eid, meta in events_meta.items()}
    folds, event_rows, config_freq = [], [], Counter()
    for block in source.get("spatial_blocks") or []:
        held_bid = str(block["block_id"]); held_ids = [str(x) for x in block["event_ids"]]
        held_events, train_events = [], []
        for eid, meta in events_meta.items():
            row = {"id": eid, "lat": meta.get("lat"), "lon": meta.get("lon"), "last_detection": meta.get("last_detection")}
            (held_events if eid in held_ids else train_events).append(row)
        kept, embargoed, embargo_meta = v3.embargo_tuning(train_events, held_events)
        kept_ids = {str(x["id"]) for x in kept if str(x["id"]) in by_doc and by_doc[str(x["id"])].get("status") == "ok"}
        selected, selection_meta = select_config(docs, event_to_block, kept_ids)
        cid = str(selected["config_id"]); config_freq[cid] += 1
        held_eval = []
        for eid in held_ids:
            doc = by_doc.get(eid)
            if not doc or doc.get("status") != "ok":
                continue
            sr, br = row_for(doc, cid), row_for(doc, "baseline_v4b")
            if not sr or not br:
                continue
            row = {"event_id": eid, "spatial_block_id": held_bid, "selected_config_id": cid, "transport_score": {k: sr.get(k) for k in METRICS}, "baseline_v4b": {k: br.get(k) for k in METRICS}, "score_area_km2": sr.get("geometric_area_km2"), "baseline_area_km2": br.get("geometric_area_km2"), "area_match_error_fraction": sr.get("area_match_error_fraction")}
            held_eval.append(row); event_rows.append(row)
        folds.append({"spatial_block_id": held_bid, "held_event_count": len(held_ids), "held_events_ok": len(held_eval), "selected_config_id": cid, "selection": selection_meta, "embargo": embargo_meta, "embargo_excluded_count": len(embargoed), "transport_score": {k: median([(x.get("transport_score") or {}).get(k) for x in held_eval]) for k in METRICS}, "baseline_v4b": {k: median([(x.get("baseline_v4b") or {}).get(k) for x in held_eval]) for k in METRICS}, "area_match_error_fraction": median([x.get("area_match_error_fraction") for x in held_eval])})
    ok_folds = [x for x in folds if int(x.get("held_events_ok") or 0) > 0]
    if not ok_folds:
        raise RuntimeError("No held spatial blocks completed")
    max_freq = max(config_freq.values()); modal_ids = sorted([cid for cid, n in config_freq.items() if n == max_freq])
    all_ok_ids = {eid for eid, doc in by_doc.items() if doc.get("status") == "ok"}
    full_summaries = {x["config_id"]: x for x in block_config_summary(docs, event_to_block, all_ok_ids)}
    modal_ids.sort(key=lambda cid: (-float((full_summaries.get(cid, {}).get("csi80") or {}).get("median") or -1), -float((full_summaries.get(cid, {}).get("precision80") or {}).get("median") or -1), cid))
    locked_id = modal_ids[0]; locked_summary = full_summaries[locked_id]
    locked = {"schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1", "locked_config_id": locked_id, "selection_source": "modal LOSO spatial-block choice on frozen 45-fire V4 universe; tie broken by full-data block-median CSI then precision", "fold_selection_frequency": dict(config_freq), "parameters": {"sigma_km": locked_summary.get("sigma_km"), "travel_tau_h": locked_summary.get("travel_tau_h"), "frp_weighting": locked_summary.get("frp_weighting"), "competitor_masking": locked_summary.get("competitor_masking"), "seed_recency_tau_h": SEED_RECENCY_TAU_H, "competitor_buffer_km": COMPETITOR_BUFFER_KM, "grid_km": GRID_KM}}
    ablations = defaultdict(list)
    for s in full_summaries.values():
        ablations[f"frp{int(bool(s.get('frp_weighting')))}_comp{int(bool(s.get('competitor_masking')))}"].append(s)
    ablation_best = {}
    for key, rows in ablations.items():
        rows = sorted(rows, key=lambda x: (-float((x.get("csi80") or {}).get("median") or -1), str(x["config_id"])))
        ablation_best[key] = rows[0]
    summary = {
        "schema_version": "1.0", "experiment": "WILDFIRE_TRANSPORT_SCORE_V1", "status": "experimental_only", "source_event_count": len(events_meta),
        "completed_event_count": sum(d.get("status") == "ok" for d in by_doc.values()),
        "excluded_events": [{"event_id": eid, "reason": doc.get("reason")} for eid, doc in sorted(by_doc.items()) if doc.get("status") != "ok"],
        "spatial_block_count": len(ok_folds), "fold_selection_frequency": dict(config_freq), "locked_config_id_for_satellite_validation": locked_id,
        "block_weighted_performance": {"transport_score": {k: dist([(x.get("transport_score") or {}).get(k) for x in ok_folds]) for k in METRICS}, "baseline_v4b": {k: dist([(x.get("baseline_v4b") or {}).get(k) for x in ok_folds]) for k in METRICS}, "paired_difference": {k: bootstrap_block_differences(ok_folds, k) for k in METRICS}},
        "area_match_error_fraction": dist([x.get("area_match_error_fraction") for x in ok_folds]), "ablation_best_by_frp_competitor": ablation_best, "folds": folds, "event_rows": event_rows,
        "guardrail": "CAMS is semi-independent and is used only for 45-fire development. The locked configuration is evaluated next against the frozen 24-fire observation-only satellite sample without retuning."
    }
    out = Path(args.output_dir); dump(out / "transport_score_45_summary.json", summary); dump(out / "locked_transport_score_v1.json", locked)
    print(json.dumps({"completed_event_count": summary["completed_event_count"], "spatial_block_count": summary["spatial_block_count"], "locked_config_id": locked_id, "paired_difference": summary["block_weighted_performance"]["paired_difference"]}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__); sub = ap.add_subparsers(dest="mode", required=True)
    q = sub.add_parser("discover"); q.add_argument("--discovery-json", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=discover)
    q = sub.add_parser("score-event"); q.add_argument("--event-id", required=True); q.add_argument("--v4-event-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=score_event)
    q = sub.add_parser("summarize45"); q.add_argument("--discovery-json", required=True); q.add_argument("--input-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=summarize45)
    return ap


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
