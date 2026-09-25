#!/usr/bin/env python3
"""Multi-event wildfire downwind validation benchmark.

Research prototype only. The benchmark enforces a tuning/test split before
model scoring, tunes only on the tuning subset, locks one parameter set, and
then evaluates that locked set on the untouched test subset.

Primary tuning metric: event-median CAMS PM2.5 >= local 80th-percentile CSI.
Tie-breaker: event-median PM2.5 corridor/background enrichment ratio.

This is a screening benchmark, not smoke-source attribution or health exposure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import Point, mapping
from shapely.ops import unary_union

import enrich_hazard_exposure as exposure
import probe_wildfire_downwind as base
import probe_wildfire_downwind_v2 as v2
import probe_wildfire_downwind_v3 as v3
import probe_wildfire_downwind_validation as val

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
HORIZONS = (12, 24, 48)
RADII_KM = (10, 20, 30, 50)
HEIGHT_MODES = ("100m", "925hPa", "850hPa", "all3")
BENCHMARK_SALT = "climate-pulse-wildfire-benchmark-v1"
PRIMARY_PERCENTILE = 80


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_hash(text: str) -> str:
    return hashlib.sha256(f"{BENCHMARK_SALT}|{text}".encode()).hexdigest()


def lat_stratum(event: dict[str, Any]) -> str:
    lat = float(event.get("lat") or 0)
    hemi = "N" if lat >= 0 else "S"
    a = abs(lat)
    band = "tropical" if a < 23.5 else "mid" if a < 55 else "high"
    return f"{hemi}_{band}"


def split_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[lat_stratum(event)].append(event)
    tune, test = [], []
    for stratum in sorted(groups):
        rows = sorted(groups[stratum], key=lambda e: stable_hash(str(e["id"])))
        for i, event in enumerate(rows):
            (test if i % 3 == 2 else tune).append(event)
    return tune, test


def candidates(snapshot: dict[str, Any], target: int) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for event in snapshot.get("canonical_events") or []:
        if not isinstance(event, dict) or event.get("type") != "Wildfire":
            continue
        eid = str(event.get("id") or "")
        if not eid or eid in seen:
            continue
        try:
            burned = float(event.get("burned_area_ha") or 0)
        except (TypeError, ValueError):
            burned = 0
        fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
        qc = event.get("footprint_qc") if isinstance(event.get("footprint_qc"), dict) else {}
        qc_pass = fp.get("qc_status") == "pass" or qc.get("status") == "pass"
        rel = fp.get("path")
        if burned < 10_000 or not qc_pass or not rel or not (ROOT / str(rel)).exists():
            continue
        seen.add(eid)
        rows.append(event)
    rows.sort(key=lambda e: stable_hash(str(e["id"])))
    return rows[:target]


def github_output(name: str, value: str) -> None:
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def discover(args: argparse.Namespace) -> int:
    rows = candidates(load(LATEST), args.target)
    tune, test = split_events(rows)
    doc = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "target": args.target,
        "candidate_count": len(rows),
        "tune_count": len(tune),
        "test_count": len(test),
        "eligibility_gate": "Wildfire; burned area >=10,000 ha; stored footprint exists; wildfire footprint QC pass.",
        "split_rule": "Deterministic 2:1 tuning:test assignment within hemisphere x latitude-band strata using SHA256 order.",
        "tune_events": [{"event_id": e["id"], "region": e.get("region"), "stratum": lat_stratum(e)} for e in tune],
        "test_events": [{"event_id": e["id"], "region": e.get("region"), "stratum": lat_stratum(e)} for e in test],
    }
    out = Path(args.output_dir)
    dump(out / "discovery.json", doc)
    github_output("tune_matrix", json.dumps([{"event_id": e["id"]} for e in tune], separators=(",", ":")))
    github_output("test_matrix", json.dumps([{"event_id": e["id"]} for e in test], separators=(",", ":")))
    github_output("candidate_count", str(len(rows)))
    print(json.dumps(doc, indent=2))
    return 0


def selected_tracks(tracks: dict[str, dict[str, list[list[float]]]], mode: str):
    return tracks if mode == "all3" else {mode: tracks[mode]}


def envelope(event: dict[str, Any], tracks_h, mode: str, radius_km: float):
    return val.multiheight_envelope(event, selected_tracks(tracks_h, mode), radius_km)


def baseline_envelope(event: dict[str, Any], seeds: list[dict[str, Any]], radius_km: float):
    fwd, inv = base.local_transformers(float(event["lat"]), float(event["lon"]))
    geoms = []
    for seed in seeds:
        p = Point(float(seed["lon"]), float(seed["lat"]))
        geoms.append(base.to_local(p, fwd).buffer(float(radius_km) * 1000.0))
    return base.to_wgs84(unary_union(geoms), inv)


def fixed_regions(event: dict[str, Any], max_envelope):
    fwd, inv = base.local_transformers(float(event["lat"]), float(event["lon"]))
    local = base.to_local(max_envelope, fwd)
    validation = local.buffer(200_000.0)
    control = validation.difference(local.buffer(75_000.0))
    return base.to_wgs84(control, inv), base.to_wgs84(validation, inv)


def fc_results_by_id(info: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out = {}
    for feat in (info or {}).get("features", []):
        props = feat.get("properties") or {}
        if props.get("config_id") is not None:
            out[str(props["config_id"])] = props
    return out


def batch_primary_metrics(ee: Any, cams_image: Any, configs, control, validation) -> list[dict[str, Any]]:
    pm = cams_image.select(val.CAMS_PM25)
    bg = val.region_stat(ee, cams_image, val.CAMS_PM25, control, ee.Reducer.mean())
    threshold = val.region_stat(ee, cams_image, val.CAMS_PM25, validation, ee.Reducer.percentile([PRIMARY_PERCENTILE]))
    if threshold is None:
        raise RuntimeError("CAMS PM2.5 percentile threshold unavailable")
    high = pm.gte(threshold)
    high_total = val.image_area(ee, high, validation)
    features = ee.FeatureCollection([
        ee.Feature(ee.Geometry(mapping(geom)), {"config_id": cid}) for cid, geom, _ in configs
    ])
    means = pm.reduceRegions(collection=features, reducer=ee.Reducer.mean(), scale=val.CAMS_SCALE_M, tileScale=4).getInfo()
    pred = ee.Image.pixelArea().updateMask(pm.mask()).reduceRegions(collection=features, reducer=ee.Reducer.sum(), scale=val.CAMS_SCALE_M, tileScale=4).getInfo()
    inter = ee.Image.pixelArea().updateMask(high).reduceRegions(collection=features, reducer=ee.Reducer.sum(), scale=val.CAMS_SCALE_M, tileScale=4).getInfo()
    means_by, pred_by, inter_by = fc_results_by_id(means), fc_results_by_id(pred), fc_results_by_id(inter)
    rows = []
    for cid, _geom, meta in configs:
        mean_val = means_by.get(cid, {}).get("mean")
        pred_area = pred_by.get(cid, {}).get("sum")
        intersection = inter_by.get(cid, {}).get("sum")
        try:
            mean_val = float(mean_val) if mean_val is not None else None
            pred_area = float(pred_area) if pred_area is not None else None
            intersection = float(intersection) if intersection is not None else None
        except (TypeError, ValueError):
            mean_val = pred_area = intersection = None
        enrichment = val.safe_ratio(mean_val, bg)
        precision = val.safe_ratio(intersection, pred_area)
        recall = val.safe_ratio(intersection, high_total)
        union = pred_area + high_total - intersection if pred_area is not None and high_total is not None and intersection is not None else None
        csi = val.safe_ratio(intersection, union)
        rows.append({
            **meta,
            "config_id": cid,
            "pm25_corridor_mean_ug_m3": round(mean_val * 1e9, 3) if mean_val is not None else None,
            "pm25_background_mean_ug_m3": round(bg * 1e9, 3) if bg is not None else None,
            "pm25_enrichment_ratio": round(enrichment, 4) if enrichment is not None else None,
            "precision80": round(precision, 4) if precision is not None else None,
            "recall80": round(recall, 4) if recall is not None else None,
            "csi80": round(csi, 4) if csi is not None else None,
            "pm25_80th_threshold_ug_m3": round(threshold * 1e9, 3),
        })
    return rows


def prepare_event(event_id: str):
    ee, project = base.initialize_ee()
    event = base.find_event(base.load_snapshot(), event_id)
    if event.get("type") != "Wildfire":
        raise RuntimeError(f"Not wildfire: {event_id}")
    perimeter, perimeter_source, episode = v2.load_event_perimeter_stored_first(event)
    seeds, fire_meta = v3.collect_fire_seeds_footprint(ee, event, perimeter, datetime.now(timezone.utc), 120.0, 30)
    run, creation_ms, leads = base.latest_ifs_run(ee, datetime.now(timezone.utc), 48)
    tracks, transport_diag = val.simulate_multilevel(ee, run, leads, seeds)
    return ee, project, event, perimeter, perimeter_source, episode, seeds, fire_meta, run, creation_ms, leads, tracks, transport_diag


def tune_event(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    try:
        ee, project, event, perimeter, perimeter_source, episode, seeds, fire_meta, run, creation_ms, leads, tracks, transport_diag = prepare_event(args.event_id)
        all_rows = []
        for horizon in HORIZONS:
            th = val.tracks_to_horizon(tracks, leads, horizon)
            max_env = envelope(event, th, "all3", 50)
            control, validation = fixed_regions(event, max_env)
            valid_time = val.millis_to_dt(creation_ms) + timedelta(hours=horizon)
            cams_image, cams_ms, offset = val.nearest_cams_image(ee, valid_time)
            if cams_image is None:
                continue
            configs = []
            for height in HEIGHT_MODES:
                for radius in RADII_KM:
                    configs.append((
                        f"model|{height}|r{radius}|h{horizon}",
                        envelope(event, th, height, radius),
                        {"family": "model", "height_mode": height, "radius_km": radius, "horizon_h": horizon},
                    ))
            for radius in RADII_KM:
                configs.append((
                    f"baseline|seed_circle|r{radius}|h{horizon}",
                    baseline_envelope(event, seeds, radius),
                    {"family": "baseline", "height_mode": "none", "radius_km": radius, "horizon_h": horizon},
                ))
            all_rows.extend(batch_primary_metrics(ee, cams_image, configs, control, validation))
        doc = {
            "schema_version": "1.0", "status": "ok", "generated_at": now_iso(),
            "event": {"id": event["id"], "region": event.get("region"), "lat": event.get("lat"), "lon": event.get("lon"), "burned_area_ha": event.get("burned_area_ha"), "last_detection": event.get("last_detection"), "perimeter_source": perimeter_source, "episode": episode},
            "fire_source": {**fire_meta, "selected_seed_count": len(seeds)},
            "ifs": {"run_creation_time": val.iso(val.millis_to_dt(creation_ms)), "levels": list(val.LEVELS), "transport_diagnostics": transport_diag},
            "tuning_rows": all_rows,
        }
        dump(out / f"{args.event_id}.json", doc)
        print(json.dumps({"status": "ok", "event_id": args.event_id, "rows": len(all_rows)}, indent=2))
    except Exception as exc:
        doc = {"schema_version": "1.0", "status": "excluded", "generated_at": now_iso(), "event": {"id": args.event_id}, "reason": f"{type(exc).__name__}: {str(exc)[:1000]}"}
        dump(out / f"{args.event_id}.json", doc)
        print(json.dumps(doc, indent=2))
    return 0


def quantile(values: list[float], q: float) -> float | None:
    vals = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    p = (len(vals) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    return vals[lo] if lo == hi else vals[lo] * (hi - p) + vals[hi] * (p - lo)


def dist(values: list[float]) -> dict[str, Any]:
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return {"n": len(vals), "p10": round(quantile(vals, 0.10), 4) if vals else None, "p25": round(quantile(vals, 0.25), 4) if vals else None, "median": round(quantile(vals, 0.50), 4) if vals else None, "p75": round(quantile(vals, 0.75), 4) if vals else None, "p90": round(quantile(vals, 0.90), 4) if vals else None}


def read_jsons(folder: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(folder.rglob("*.json")):
        try:
            out.append(load(p))
        except Exception:
            pass
    return out


def config_summary(docs: list[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        if doc.get("status") != "ok":
            continue
        event_id = (doc.get("event") or {}).get("id")
        for row in doc.get("tuning_rows") or []:
            if row.get("family") == family:
                by[str(row["config_id"])].append({"event_id": event_id, **row})
    summaries = []
    for cid, rows in by.items():
        first = rows[0]
        summaries.append({
            "config_id": cid, "family": family, "height_mode": first.get("height_mode"), "radius_km": first.get("radius_km"), "horizon_h": first.get("horizon_h"), "events_n": len(rows),
            "csi80": dist([r.get("csi80") for r in rows]), "precision80": dist([r.get("precision80") for r in rows]), "recall80": dist([r.get("recall80") for r in rows]), "pm25_enrichment_ratio": dist([r.get("pm25_enrichment_ratio") for r in rows]),
        })
    summaries.sort(key=lambda s: (-(s["csi80"]["median"] if s["csi80"]["median"] is not None else -1), -(s["pm25_enrichment_ratio"]["median"] if s["pm25_enrichment_ratio"]["median"] is not None else -1), s["config_id"]))
    return summaries


def select(args: argparse.Namespace) -> int:
    docs = read_jsons(Path(args.input_dir))
    ok = [d for d in docs if d.get("status") == "ok"]
    excluded = [d for d in docs if d.get("status") != "ok"]
    model = config_summary(ok, "model")
    if not model:
        raise RuntimeError("No valid tuning model rows")
    locked = model[0]
    baseline_all = config_summary(ok, "baseline")
    baseline_same_h = [b for b in baseline_all if int(b["horizon_h"]) == int(locked["horizon_h"])]
    locked_base = baseline_same_h[0] if baseline_same_h else None
    doc = {
        "schema_version": "1.0", "generated_at": now_iso(),
        "selection_rule": "Maximize event-median PM2.5 80th-percentile CSI on tuning fires; tie-break by median PM2.5 enrichment ratio. Baseline radius is tuned only at the locked model horizon.",
        "tuning_events_ok": len(ok), "tuning_events_excluded": len(excluded),
        "locked_model": locked, "locked_baseline": locked_base,
        "all_model_configs": model, "all_baseline_configs": baseline_all,
        "excluded": [{"event_id": (d.get("event") or {}).get("id"), "reason": d.get("reason")} for d in excluded],
    }
    out = Path(args.output_dir)
    dump(out / "locked_selection.json", doc)
    github_output("locked_config_id", locked["config_id"])
    print(json.dumps({"tuning_events_ok": len(ok), "tuning_events_excluded": len(excluded), "locked_model": locked, "locked_baseline": locked_base}, indent=2))
    return 0


def config_geom(event, seeds, tracks, leads, config: dict[str, Any]):
    horizon = int(config["horizon_h"])
    radius = float(config["radius_km"])
    th = val.tracks_to_horizon(tracks, leads, horizon)
    if config.get("family") == "baseline":
        return baseline_envelope(event, seeds, radius), th
    return envelope(event, th, str(config["height_mode"]), radius), th


def test_event(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    lockdoc = load(Path(args.locked_json))
    locked, baseline = lockdoc["locked_model"], lockdoc.get("locked_baseline")
    try:
        ee, project, event, perimeter, perimeter_source, episode, seeds, fire_meta, run, creation_ms, leads, tracks, transport_diag = prepare_event(args.event_id)
        horizon = int(locked["horizon_h"])
        model_geom, th = config_geom(event, seeds, tracks, leads, locked)
        max_env = envelope(event, th, "all3", 50)
        control, validation = fixed_regions(event, max_env)
        valid_time = val.millis_to_dt(creation_ms) + timedelta(hours=horizon)
        cams_image, cams_ms, offset = val.nearest_cams_image(ee, valid_time)
        if cams_image is None:
            raise RuntimeError("No CAMS image near locked valid time")
        full_model = val.cams_validation(ee, cams_image, model_geom, control, validation)
        pop_path = exposure.ensure_population_source()
        model_pop = base.population(pop_path, model_geom)
        base_eval = None
        if baseline:
            baseline_geom, _ = config_geom(event, seeds, tracks, leads, baseline)
            base_eval = val.cams_validation(ee, cams_image, baseline_geom, control, validation)
        doc = {
            "schema_version": "1.0", "status": "ok", "generated_at": now_iso(),
            "event": {"id": event["id"], "region": event.get("region"), "lat": event.get("lat"), "lon": event.get("lon"), "burned_area_ha": event.get("burned_area_ha"), "last_detection": event.get("last_detection"), "perimeter_source": perimeter_source, "episode": episode},
            "fire_source": {**fire_meta, "selected_seed_count": len(seeds)},
            "ifs": {"run_creation_time": val.iso(val.millis_to_dt(creation_ms)), "transport_diagnostics": transport_diag},
            "locked_model": locked, "locked_baseline": baseline,
            "valid_time": val.iso(valid_time), "cams_image_time": val.iso(val.millis_to_dt(cams_ms)) if cams_ms else None, "cams_time_offset_hours": offset,
            "screening_population": model_pop, "model_metrics": full_model, "baseline_metrics": base_eval,
        }
        dump(out / f"{args.event_id}.json", doc)
        print(json.dumps({"status": "ok", "event_id": args.event_id, "screening_population": model_pop, "model_pm25": full_model.get("pm25")}, indent=2))
    except Exception as exc:
        doc = {"schema_version": "1.0", "status": "excluded", "generated_at": now_iso(), "event": {"id": args.event_id}, "reason": f"{type(exc).__name__}: {str(exc)[:1000]}", "locked_model": locked}
        dump(out / f"{args.event_id}.json", doc)
        print(json.dumps(doc, indent=2))
    return 0


def metric_from_test(doc: dict[str, Any], baseline: bool, key: str) -> float | None:
    root = doc.get("baseline_metrics") if baseline else doc.get("model_metrics")
    if not isinstance(root, dict):
        return None
    pm = root.get("pm25") or {}
    if key == "enrichment":
        return pm.get("enrichment_ratio")
    p80 = ((pm.get("spatial_overlap") or {}).get("percentiles") or {}).get("80") or {}
    return {"precision80": p80.get("precision_corridor_high_fraction"), "recall80": p80.get("recall_local_high_area_captured"), "csi80": p80.get("critical_success_index")}.get(key)


def aerosol_enrichment(doc: dict[str, Any], key: str) -> float | None:
    root = doc.get("model_metrics") or {}
    return (root.get(key) or {}).get("enrichment_ratio")


def paired_bootstrap(docs: list[dict[str, Any]], key: str, n: int = 5000) -> dict[str, Any]:
    pairs = []
    for d in docs:
        if d.get("status") != "ok" or not d.get("baseline_metrics"):
            continue
        a, b = metric_from_test(d, False, key), metric_from_test(d, True, key)
        if a is not None and b is not None:
            pairs.append((float(a), float(b)))
    if not pairs:
        return {"n": 0, "median_difference": None, "bootstrap_95ci": None}
    diffs = [a - b for a, b in pairs]
    rng = random.Random(20260914)
    boots = [statistics.median([diffs[rng.randrange(len(diffs))] for _ in diffs]) for _ in range(n)]
    return {"n": len(pairs), "median_difference": round(statistics.median(diffs), 4), "bootstrap_95ci": [round(quantile(boots, 0.025), 4), round(quantile(boots, 0.975), 4)]}


def summarize(args: argparse.Namespace) -> int:
    discovery, lockdoc = load(Path(args.discovery_json)), load(Path(args.locked_json))
    test_docs = read_jsons(Path(args.test_dir))
    ok_test = [d for d in test_docs if d.get("status") == "ok"]
    excluded_test = [d for d in test_docs if d.get("status") != "ok"]
    def test_dist(key: str, baseline: bool = False):
        return dist([metric_from_test(d, baseline, key) for d in ok_test])
    populations = [float(d["screening_population"]) for d in ok_test if d.get("screening_population") is not None]
    eligible_total = int(lockdoc.get("tuning_events_ok") or 0) + len(ok_test)
    status = "benchmark_complete" if eligible_total >= 30 else "benchmark_sample_below_target"
    doc = {
        "schema_version": "1.0", "status": status, "generated_at": now_iso(),
        "candidate_count": discovery.get("candidate_count"), "eligible_total": eligible_total,
        "tuning_events_ok": lockdoc.get("tuning_events_ok"), "tuning_events_excluded": lockdoc.get("tuning_events_excluded"), "test_events_ok": len(ok_test), "test_events_excluded": len(excluded_test),
        "locked_model": lockdoc.get("locked_model"), "locked_baseline": lockdoc.get("locked_baseline"),
        "out_of_sample_model": {
            "pm25_enrichment_ratio": test_dist("enrichment"), "precision80": test_dist("precision80"), "recall80": test_dist("recall80"), "csi80": test_dist("csi80"),
            "total_aod550_enrichment_ratio": dist([aerosol_enrichment(d, "total_aod550") for d in ok_test]),
            "fire_related_aod_proxy_enrichment_ratio": dist([aerosol_enrichment(d, "fire_related_aod_proxy") for d in ok_test]),
            "screening_population": dist(populations),
        },
        "out_of_sample_baseline": {"pm25_enrichment_ratio": test_dist("enrichment", True), "precision80": test_dist("precision80", True), "recall80": test_dist("recall80", True), "csi80": test_dist("csi80", True)},
        "paired_model_minus_baseline": {k: paired_bootstrap(ok_test, k) for k in ("enrichment", "precision80", "recall80", "csi80")},
        "excluded_test": [{"event_id": (d.get("event") or {}).get("id"), "reason": d.get("reason")} for d in excluded_test],
        "interpretation_guardrail": "Population values are residents intersecting a screening corridor, not confirmed smoke exposure. CAMS is a semi-independent model target, not observation-only truth.",
    }
    out = Path(args.output_dir)
    dump(out / "benchmark_summary.json", doc)
    dump(out / "test_event_results.json", ok_test)
    print(json.dumps(doc, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    q = sub.add_parser("discover"); q.add_argument("--target", type=int, default=45); q.add_argument("--output-dir", required=True); q.set_defaults(func=discover)
    q = sub.add_parser("tune-event"); q.add_argument("--event-id", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=tune_event)
    q = sub.add_parser("select"); q.add_argument("--input-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=select)
    q = sub.add_parser("test-event"); q.add_argument("--event-id", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=test_event)
    q = sub.add_parser("summarize"); q.add_argument("--discovery-json", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--tune-dir", required=True); q.add_argument("--test-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=summarize)
    return p


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
