#!/usr/bin/env python3
"""Grouped/compactness-regularized wildfire downwind benchmark (v2).

Research prototype only.

This round simultaneously addresses two weaknesses exposed by v1:
1. geographic/temporal leakage: countries are atomic between tuning and test,
   with a metadata-only spatiotemporal embargo on nearby concurrent tuning fires;
2. over-broad corridors: selection uses a predeclared parsimony rule, choosing
   the smallest median predicted area among configurations retaining at least
   95% of the best tuning-set median CSI80.

The test set is frozen before any scoring and receives only the locked model.
CAMS remains a semi-independent validation target, not observation-only truth.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

import benchmark_wildfire_downwind as v1

LATEST = v1.LATEST
HORIZONS = v1.HORIZONS
RADII_KM = v1.RADII_KM
HEIGHT_MODES = v1.HEIGHT_MODES
PRIMARY_PERCENTILE = v1.PRIMARY_PERCENTILE
BENCHMARK_SALT = "climate-pulse-wildfire-benchmark-v2-grouped-compactness"
CSI_RETENTION_FRACTION = 0.95
MIN_TEST_REGIONS = 3
MIN_TUNING_EVENTS_AFTER_EMBARGO = 20
EMBARGO_CANDIDATES = ((500.0, 72.0), (350.0, 48.0), (250.0, 24.0), (0.0, 0.0))


def stable_hash(text: str) -> str:
    return hashlib.sha256(f"{BENCHMARK_SALT}|{text}".encode()).hexdigest()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event_time(event: dict[str, Any]) -> datetime | None:
    for key in ("last_detection", "event_date", "event_start", "source_updated_at"):
        dt = parse_dt(event.get(key))
        if dt is not None:
            return dt
    return None


def region_key(event: dict[str, Any]) -> str:
    return str(event.get("region") or "UNKNOWN").strip() or "UNKNOWN"


def haversine_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1, lon1 = math.radians(float(a.get("lat") or 0.0)), math.radians(float(a.get("lon") or 0.0))
    lat2, lon2 = math.radians(float(b.get("lat") or 0.0)), math.radians(float(b.get("lon") or 0.0))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 6371.0088 * 2.0 * math.asin(min(1.0, math.sqrt(h)))


def choose_test_regions(events: list[dict[str, Any]]) -> tuple[set[str], dict[str, Any]]:
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_region[region_key(event)].append(event)
    regions = sorted(by_region)
    if len(regions) < MIN_TEST_REGIONS + 1:
        raise RuntimeError(f"Need >= {MIN_TEST_REGIONS + 1} regions for grouped split; found {len(regions)}")
    target = max(1, round(len(events) / 3))
    best = None
    best_combo = None
    for r in range(MIN_TEST_REGIONS, max(MIN_TEST_REGIONS, len(regions) - 2) + 1):
        for combo in itertools.combinations(regions, r):
            n_test = sum(len(by_region[x]) for x in combo)
            n_tune_regions = len(regions) - r
            if n_test <= 0 or n_test >= len(events) or n_tune_regions < 2:
                continue
            score = (abs(n_test - target), -n_tune_regions, stable_hash("|".join(combo)))
            if best is None or score < best:
                best, best_combo = score, combo
    if best_combo is None:
        raise RuntimeError("Unable to construct grouped country holdout")
    return set(best_combo), {
        "target_test_events": target,
        "selected_test_regions": list(best_combo),
        "region_event_counts": {r: len(by_region[r]) for r in regions},
        "selection_rule": "Countries/regions are atomic. Choose a region subset closest to one-third of events; among ties preserve the largest number of distinct tuning regions; deterministic hash tie-break.",
    }


def embargo_tuning(tune: list[dict[str, Any]], test: list[dict[str, Any]]):
    for radius_km, hours in EMBARGO_CANDIDATES:
        kept, excluded = [], []
        for event in tune:
            hit = None
            if radius_km > 0 and hours > 0:
                te = event_time(event)
                for held in test:
                    th = event_time(held)
                    if te is None or th is None:
                        continue
                    delta_h = abs((te - th).total_seconds()) / 3600.0
                    if delta_h <= hours:
                        d_km = haversine_km(event, held)
                        if d_km <= radius_km:
                            hit = {
                                "event_id": event.get("id"), "region": region_key(event),
                                "near_test_event_id": held.get("id"), "near_test_region": region_key(held),
                                "distance_km": round(d_km, 1), "time_difference_h": round(delta_h, 1),
                            }
                            break
            (kept if hit is None else excluded).append(event if hit is None else hit)
        if len(kept) >= MIN_TUNING_EVENTS_AFTER_EMBARGO or radius_km == 0:
            return kept, excluded, {
                "radius_km": radius_km,
                "time_window_hours": hours,
                "minimum_tuning_events": MIN_TUNING_EVENTS_AFTER_EMBARGO,
                "rule": "Exclude a non-test-country tuning fire when it is both spatially close and temporally concurrent with any held-out fire. Use the strongest predeclared embargo that retains at least 20 tuning events.",
            }
    raise RuntimeError("Embargo selection failed")


def discover(args: argparse.Namespace) -> int:
    rows = v1.candidates(v1.load(LATEST), args.target)
    test_regions, split_meta = choose_test_regions(rows)
    test = [e for e in rows if region_key(e) in test_regions]
    tune_pre = [e for e in rows if region_key(e) not in test_regions]
    tune, embargoed, embargo_meta = embargo_tuning(tune_pre, test)
    tune_regions = sorted({region_key(e) for e in tune})
    test_regions_actual = sorted({region_key(e) for e in test})
    overlap = sorted(set(tune_regions) & set(test_regions_actual))
    if overlap:
        raise RuntimeError(f"Country leakage after grouped split: {overlap}")
    fmt = lambda e: {
        "event_id": e["id"], "region": region_key(e), "lat": e.get("lat"), "lon": e.get("lon"),
        "event_time": event_time(e).isoformat().replace("+00:00", "Z") if event_time(e) else None,
    }
    doc = {
        "schema_version": "2.0", "generated_at": v1.now_iso(), "target": args.target,
        "candidate_count": len(rows), "tune_count_pre_embargo": len(tune_pre), "tune_count": len(tune),
        "test_count": len(test), "embargo_excluded_count": len(embargoed),
        "eligibility_gate": "Wildfire; burned area >=10,000 ha; stored footprint exists; wildfire footprint QC pass.",
        "grouped_split": split_meta, "embargo": embargo_meta, "country_overlap_check": overlap,
        "tune_regions": tune_regions, "test_regions": test_regions_actual, "embargo_excluded": embargoed,
        "tune_events": [fmt(e) for e in tune], "test_events": [fmt(e) for e in test],
    }
    out = Path(args.output_dir)
    v1.dump(out / "discovery.json", doc)
    v1.github_output("tune_matrix", json.dumps([{"event_id": e["id"]} for e in tune], separators=(",", ":")))
    v1.github_output("test_matrix", json.dumps([{"event_id": e["id"]} for e in test], separators=(",", ":")))
    v1.github_output("candidate_count", str(len(rows)))
    print(json.dumps(doc, indent=2))
    return 0


def batch_primary_metrics(ee: Any, cams_image: Any, configs, control, validation):
    pm = cams_image.select(v1.val.CAMS_PM25)
    bg = v1.val.region_stat(ee, cams_image, v1.val.CAMS_PM25, control, ee.Reducer.mean())
    threshold = v1.val.region_stat(ee, cams_image, v1.val.CAMS_PM25, validation, ee.Reducer.percentile([PRIMARY_PERCENTILE]))
    if threshold is None:
        raise RuntimeError("CAMS PM2.5 percentile threshold unavailable")
    high = pm.gte(threshold)
    high_total = v1.val.image_area(ee, high, validation)
    features = ee.FeatureCollection([ee.Feature(ee.Geometry(mapping(geom)), {"config_id": cid}) for cid, geom, _ in configs])
    means = pm.reduceRegions(collection=features, reducer=ee.Reducer.mean(), scale=v1.val.CAMS_SCALE_M, tileScale=4).getInfo()
    pred = ee.Image.pixelArea().updateMask(pm.mask()).reduceRegions(collection=features, reducer=ee.Reducer.sum(), scale=v1.val.CAMS_SCALE_M, tileScale=4).getInfo()
    inter = ee.Image.pixelArea().updateMask(high).reduceRegions(collection=features, reducer=ee.Reducer.sum(), scale=v1.val.CAMS_SCALE_M, tileScale=4).getInfo()
    means_by, pred_by, inter_by = v1.fc_results_by_id(means), v1.fc_results_by_id(pred), v1.fc_results_by_id(inter)
    rows = []
    for cid, _geom, meta in configs:
        mean_val, pred_area, intersection = means_by.get(cid, {}).get("mean"), pred_by.get(cid, {}).get("sum"), inter_by.get(cid, {}).get("sum")
        try:
            mean_val = float(mean_val) if mean_val is not None else None
            pred_area = float(pred_area) if pred_area is not None else None
            intersection = float(intersection) if intersection is not None else None
        except (TypeError, ValueError):
            mean_val = pred_area = intersection = None
        enrichment = v1.val.safe_ratio(mean_val, bg)
        precision = v1.val.safe_ratio(intersection, pred_area)
        recall = v1.val.safe_ratio(intersection, high_total)
        union = pred_area + high_total - intersection if pred_area is not None and high_total is not None and intersection is not None else None
        csi = v1.val.safe_ratio(intersection, union)
        rows.append({
            **meta, "config_id": cid,
            "pm25_corridor_mean_ug_m3": round(mean_val * 1e9, 3) if mean_val is not None else None,
            "pm25_background_mean_ug_m3": round(bg * 1e9, 3) if bg is not None else None,
            "pm25_enrichment_ratio": round(enrichment, 4) if enrichment is not None else None,
            "precision80": round(precision, 4) if precision is not None else None,
            "recall80": round(recall, 4) if recall is not None else None,
            "csi80": round(csi, 4) if csi is not None else None,
            "predicted_area_km2": round(pred_area / 1e6, 3) if pred_area is not None else None,
            "local_high_pm25_area_km2": round(high_total / 1e6, 3) if high_total is not None else None,
            "pm25_80th_threshold_ug_m3": round(threshold * 1e9, 3),
        })
    return rows


def tune_event(args: argparse.Namespace) -> int:
    old = v1.batch_primary_metrics
    v1.batch_primary_metrics = batch_primary_metrics
    try:
        return v1.tune_event(args)
    finally:
        v1.batch_primary_metrics = old


def config_summary(docs: list[dict[str, Any]], family: str):
    by = defaultdict(list)
    for doc in docs:
        if doc.get("status") != "ok":
            continue
        event_id = (doc.get("event") or {}).get("id")
        for row in doc.get("tuning_rows") or []:
            if row.get("family") == family:
                by[str(row["config_id"])].append({"event_id": event_id, **row})
    out = []
    for cid, rows in by.items():
        first = rows[0]
        out.append({
            "config_id": cid, "family": family, "height_mode": first.get("height_mode"),
            "radius_km": first.get("radius_km"), "horizon_h": first.get("horizon_h"), "events_n": len(rows),
            "csi80": v1.dist([r.get("csi80") for r in rows]),
            "precision80": v1.dist([r.get("precision80") for r in rows]),
            "recall80": v1.dist([r.get("recall80") for r in rows]),
            "pm25_enrichment_ratio": v1.dist([r.get("pm25_enrichment_ratio") for r in rows]),
            "predicted_area_km2": v1.dist([r.get("predicted_area_km2") for r in rows]),
        })
    out.sort(key=lambda s: (-(s["csi80"]["median"] if s["csi80"]["median"] is not None else -1), s["config_id"]))
    return out


def parsimonious_model(model, retain_fraction: float):
    max_events = max(int(m.get("events_n") or 0) for m in model)
    support_floor = max(1, math.ceil(max_events * 0.90))
    supported = [m for m in model if int(m.get("events_n") or 0) >= support_floor and m["csi80"].get("median") is not None and m["predicted_area_km2"].get("median") is not None]
    if not supported:
        raise RuntimeError("No sufficiently supported model configurations")
    best_unregularized = max(supported, key=lambda m: (float(m["csi80"]["median"]), float(m["precision80"]["median"] or -1), float(m["pm25_enrichment_ratio"]["median"] or -1)))
    max_csi = float(best_unregularized["csi80"]["median"])
    floor = max_csi * retain_fraction
    eligible = [m for m in supported if float(m["csi80"]["median"]) + 1e-12 >= floor]
    complexity = {"100m": 1, "925hPa": 1, "850hPa": 1, "all3": 3, "none": 0}
    eligible.sort(key=lambda m: (
        float(m["predicted_area_km2"]["median"]), -float(m["precision80"]["median"] or -1),
        -float(m["pm25_enrichment_ratio"]["median"] or -1), int(m["horizon_h"]), int(m["radius_km"]),
        complexity.get(str(m.get("height_mode")), 9), str(m["config_id"]),
    ))
    locked = eligible[0]
    base_area = float(best_unregularized["predicted_area_km2"]["median"])
    return locked, {
        "retain_fraction": retain_fraction, "max_tuning_median_csi80": round(max_csi, 4),
        "csi80_admissibility_floor": round(floor, 4), "support_floor_events": support_floor,
        "admissible_config_count": len(eligible), "best_unregularized": best_unregularized,
        "locked_area_reduction_fraction_vs_unregularized": round(1.0 - float(locked["predicted_area_km2"]["median"]) / base_area, 4) if base_area > 0 else None,
    }


def select(args: argparse.Namespace) -> int:
    docs = v1.read_jsons(Path(args.input_dir))
    ok, excluded = [d for d in docs if d.get("status") == "ok"], [d for d in docs if d.get("status") != "ok"]
    model = config_summary(ok, "model")
    if not model:
        raise RuntimeError("No valid tuning model rows")
    locked, regularization = parsimonious_model(model, CSI_RETENTION_FRACTION)
    baseline_all = config_summary(ok, "baseline")
    same_h = [b for b in baseline_all if int(b["horizon_h"]) == int(locked["horizon_h"])]
    locked_base = same_h[0] if same_h else None
    sensitivity = {f"retain_{int(frac * 100)}pct": {"locked_model": parsimonious_model(model, frac)[0], "meta": parsimonious_model(model, frac)[1]} for frac in (1.0, 0.95, 0.90)}
    doc = {
        "schema_version": "2.0", "generated_at": v1.now_iso(),
        "selection_rule": "Compactness-regularized selection: among configurations with >=90% tuning-event support and tuning-set median CSI80 >=95% of the best supported median CSI80, choose the smallest median predicted CAMS-valid corridor area; tie-break by higher precision80, higher PM2.5 enrichment, shorter horizon, smaller radius, and simpler height mode. Baseline radius is tuned only at the locked model horizon.",
        "tuning_events_ok": len(ok), "tuning_events_excluded": len(excluded),
        "locked_model": locked, "locked_baseline": locked_base, "regularization": regularization,
        "selection_sensitivity": sensitivity, "all_model_configs": model, "all_baseline_configs": baseline_all,
        "excluded": [{"event_id": (d.get("event") or {}).get("id"), "reason": d.get("reason")} for d in excluded],
    }
    out = Path(args.output_dir)
    v1.dump(out / "locked_selection.json", doc)
    v1.github_output("locked_config_id", locked["config_id"])
    print(json.dumps({"tuning_events_ok": len(ok), "tuning_events_excluded": len(excluded), "regularization": regularization, "locked_model": locked, "locked_baseline": locked_base}, indent=2))
    return 0


def test_event(args: argparse.Namespace) -> int:
    return v1.test_event(args)


def summarize(args: argparse.Namespace) -> int:
    rc = v1.summarize(args)
    path = Path(args.output_dir) / "benchmark_summary.json"
    doc, discovery, lockdoc = v1.load(path), v1.load(Path(args.discovery_json)), v1.load(Path(args.locked_json))
    doc.update({
        "schema_version": "2.0", "benchmark_version": "grouped_compactness_v2",
        "grouped_split": discovery.get("grouped_split"), "embargo": discovery.get("embargo"),
        "embargo_excluded_count": discovery.get("embargo_excluded_count"), "tune_regions": discovery.get("tune_regions"),
        "test_regions": discovery.get("test_regions"), "country_overlap_check": discovery.get("country_overlap_check"),
        "selection_rule": lockdoc.get("selection_rule"), "regularization": lockdoc.get("regularization"),
        "selection_sensitivity": lockdoc.get("selection_sensitivity"),
        "interpretation_guardrail": "Held-out countries/regions are absent from tuning; a pre-scoring spatiotemporal embargo removes nearby concurrent cross-border tuning fires when sample size permits. Population values are residents intersecting a screening corridor, not confirmed smoke exposure. CAMS is a semi-independent model target, not observation-only truth.",
    })
    v1.dump(path, doc)
    print(json.dumps(doc, indent=2))
    return rc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    q = sub.add_parser("discover"); q.add_argument("--target", type=int, default=45); q.add_argument("--output-dir", required=True); q.set_defaults(func=discover)
    q = sub.add_parser("tune-event"); q.add_argument("--event-id", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=tune_event)
    q = sub.add_parser("select"); q.add_argument("--input-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=select)
    q = sub.add_parser("test-event"); q.add_argument("--event-id", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=test_event)
    q = sub.add_parser("summarize"); q.add_argument("--discovery-json", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--tune-dir", required=True); q.add_argument("--test-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=summarize)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(int(args.func(args)))
