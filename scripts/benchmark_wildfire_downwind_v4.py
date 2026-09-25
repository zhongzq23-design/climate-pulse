#!/usr/bin/env python3
"""Wildfire downwind benchmark v4: spatial-block nested CV with area matching.

Research prototype only.

V4 keeps the frozen v3 coordinate-only 500 km spatial-block geometry. It does
not use country labels. The 45-event v3 universe is evaluated with leave-one-
spatial-block-out nested cross-validation. Within every outer fold, model
selection uses only the non-held blocks (after the same predeclared
spatiotemporal embargo).

Two changes target the over-broad-envelope problem:
1. selection is an equal-weight Pareto ideal-point compromise between
   event-median CSI80 (higher is better) and log geometric corridor area
   (lower is better), rather than pure CSI;
2. every candidate trajectory footprint is paired with an event-specific
   isotropic fire-seed footprint whose geometric area is matched to the model
   footprint before CAMS scoring.

The 28 newly eligible events discovered after v3 are deliberately not scored
in v4; they remain available for a later genuinely fresh validation.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from pyproj import Geod

import benchmark_wildfire_downwind as v1
import benchmark_wildfire_downwind_v2 as v2
import benchmark_wildfire_downwind_v3 as v3

LATEST = v1.LATEST
GEOD = Geod(ellps="WGS84")
AREA_WEIGHT = 0.50
MIN_SUPPORT_FRACTION = 0.90
BOOTSTRAP_N = 5000

PRIOR_V3_IDS = {
    "gdacs-WF-1031271", "gdacs-WF-1031393", "gdacs-WF-1031396", "gdacs-WF-1031413",
    "gdacs-WF-1031436", "gdacs-WF-1031454", "gdacs-WF-1031478", "gdacs-WF-1031511",
    "gdacs-WF-1031514", "gdacs-WF-1031522", "gdacs-WF-1031565", "gdacs-WF-1031577",
    "gdacs-WF-1031579", "gdacs-WF-1031636", "gdacs-WF-1031674", "gdacs-WF-1031679",
    "gdacs-WF-1031683", "gdacs-WF-1031726", "gdacs-WF-1031731", "gdacs-WF-1031736",
    "gdacs-WF-1031738", "gdacs-WF-1031759", "gdacs-WF-1031765", "gdacs-WF-1031769",
    "gdacs-WF-1031770", "gdacs-WF-1031772", "gdacs-WF-1031773", "gdacs-WF-1031776",
    "gdacs-WF-1031786", "gdacs-WF-1031815", "gdacs-WF-1031818", "gdacs-WF-1031831",
    "gdacs-WF-1031832", "gdacs-WF-1031846", "gdacs-WF-1031862", "gdacs-WF-1031877",
    "gdacs-WF-1031879", "gdacs-WF-1031884", "gdacs-WF-1031900", "gdacs-WF-1031918",
    "gdacs-WF-1031929", "gdacs-WF-1031933", "gdacs-WF-1031934", "gdacs-WF-1031948",
    "gdacs-WF-1031949",
}
EXPECTED_V3_BLOCK_IDS = {
    "SB-2832a410c8", "SB-55a02f045f", "SB-65296d06c0", "SB-6e161ea373",
    "SB-9610145536", "SB-da92b08cd7", "SB-06c78bb825", "SB-60d38027a0",
    "SB-ccaa2e7ea1", "SB-26e08aff2a", "SB-2f10f0cde7", "SB-9eefb39ab2",
    "SB-c8bc8adae4", "SB-cfca61c611", "SB-eba1986c7d", "SB-ee99436355",
}


def geodesic_area_km2(geom) -> float:
    area_m2, _ = GEOD.geometry_area_perimeter(geom)
    return abs(float(area_m2)) / 1e6


def matched_isotropic_envelope(event: dict[str, Any], seeds: list[dict[str, Any]], target_area_km2: float):
    """Find one common seed-buffer radius whose union matches target geodesic area."""
    if target_area_km2 <= 0:
        raise ValueError("target_area_km2 must be positive")

    def geom_at(radius_km: float):
        return v1.baseline_envelope(event, seeds, radius_km)

    lo, hi = 0.01, 10.0
    hi_geom = geom_at(hi)
    hi_area = geodesic_area_km2(hi_geom)
    while hi_area < target_area_km2 and hi < 2500.0:
        lo = hi
        hi *= 2.0
        hi_geom = geom_at(hi)
        hi_area = geodesic_area_km2(hi_geom)
    if hi_area < target_area_km2:
        raise RuntimeError(f"Unable to area-match target {target_area_km2:.1f} km2 below 2500 km radius")

    best_geom, best_area, best_r = hi_geom, hi_area, hi
    for _ in range(30):
        mid = (lo + hi) / 2.0
        g = geom_at(mid)
        a = geodesic_area_km2(g)
        if abs(a - target_area_km2) < abs(best_area - target_area_km2):
            best_geom, best_area, best_r = g, a, mid
        if a < target_area_km2:
            lo = mid
        else:
            hi = mid
    err = abs(best_area - target_area_km2) / target_area_km2
    return best_geom, best_r, best_area, err


def discover(args: argparse.Namespace) -> int:
    rows_all = v1.candidates(v1.load(LATEST), args.target)
    by_id = {str(e["id"]): e for e in rows_all}
    prior_rows = [by_id[eid] for eid in sorted(PRIOR_V3_IDS) if eid in by_id]
    missing = sorted(PRIOR_V3_IDS - set(by_id))
    if missing:
        raise RuntimeError(f"Frozen v3 event universe incomplete in current snapshot: {missing}")
    if len(prior_rows) != 45:
        raise RuntimeError(f"Expected 45 frozen v3 events, found {len(prior_rows)}")

    blocks = v3.make_spatial_blocks(prior_rows)
    block_ids = {str(b["block_id"]) for b in blocks}
    if block_ids != EXPECTED_V3_BLOCK_IDS:
        raise RuntimeError(f"Frozen v3 spatial-block geometry drifted: {sorted(block_ids ^ EXPECTED_V3_BLOCK_IDS)}")
    event_to_block = {eid: str(b["block_id"]) for b in blocks for eid in b["event_ids"]}

    fresh_ids = sorted(set(by_id) - PRIOR_V3_IDS)
    events = []
    for e in prior_rows:
        dt = v2.event_time(e)
        events.append({
            "event_id": str(e["id"]),
            "spatial_block_id": event_to_block[str(e["id"])],
            "lat": e.get("lat"),
            "lon": e.get("lon"),
            "last_detection": e.get("last_detection"),
            "event_time": dt.isoformat().replace("+00:00", "Z") if dt else None,
        })

    doc = {
        "schema_version": "4.0",
        "generated_at": v1.now_iso(),
        "evaluation_mode": "nested_leave_one_spatial_block_out",
        "candidate_pool_count": len(rows_all),
        "frozen_v3_event_count": len(prior_rows),
        "fresh_eligible_preserved_unscored_count": len(fresh_ids),
        "fresh_eligible_preserved_unscored_ids": fresh_ids,
        "spatial_block_definition": {
            "source": "frozen v3 geometry",
            "method": "deterministic complete-link clustering on great-circle distance",
            "max_pairwise_diameter_km": v3.BLOCK_MAX_DIAMETER_KM,
            "administrative_boundaries_used": False,
        },
        "spatial_blocks": blocks,
        "events": events,
        "guardrail": (
            "V4 scores only the already-inspected frozen 45-event v3 universe using nested spatial-block CV. "
            "Newly eligible events are enumerated but not scored, preserving them for a later fresh validation."
        ),
    }
    out = Path(args.output_dir)
    v1.dump(out / "discovery.json", doc)
    v1.github_output("grid_matrix", json.dumps([{"event_id": e["event_id"]} for e in events], separators=(",", ":")))
    print(json.dumps(doc, indent=2))
    return 0


def grid_event(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    try:
        (
            ee, project, event, perimeter, perimeter_source, episode, seeds, fire_meta,
            run, creation_ms, leads, tracks, transport_diag,
        ) = v1.prepare_event(args.event_id)

        all_rows: list[dict[str, Any]] = []
        for horizon in v1.HORIZONS:
            th = v1.val.tracks_to_horizon(tracks, leads, horizon)
            max_env = v1.envelope(event, th, "all3", 50)
            control, validation = v1.fixed_regions(event, max_env)
            valid_time = v1.val.millis_to_dt(creation_ms) + timedelta(hours=horizon)
            cams_image, cams_ms, offset = v1.val.nearest_cams_image(ee, valid_time)
            if cams_image is None:
                continue

            configs = []
            for height in v1.HEIGHT_MODES:
                for radius in v1.RADII_KM:
                    cid = f"model|{height}|r{radius}|h{horizon}"
                    model_geom = v1.envelope(event, th, height, radius)
                    model_area = geodesic_area_km2(model_geom)
                    configs.append((
                        cid,
                        model_geom,
                        {
                            "family": "model",
                            "height_mode": height,
                            "radius_km": radius,
                            "horizon_h": horizon,
                            "geometric_area_km2": round(model_area, 3),
                        },
                    ))

                    base_geom, matched_radius, base_area, match_err = matched_isotropic_envelope(
                        event, seeds, model_area
                    )
                    configs.append((
                        f"matched|{cid}",
                        base_geom,
                        {
                            "family": "matched_baseline",
                            "height_mode": "none",
                            "radius_km": round(matched_radius, 4),
                            "horizon_h": horizon,
                            "matched_to_config_id": cid,
                            "target_geometric_area_km2": round(model_area, 3),
                            "geometric_area_km2": round(base_area, 3),
                            "area_match_error_fraction": round(match_err, 8),
                        },
                    ))

            rows = v2.batch_primary_metrics(ee, cams_image, configs, control, validation)
            for r in rows:
                r["cams_time_offset_hours"] = offset
            all_rows.extend(rows)

        doc = {
            "schema_version": "4.0",
            "status": "ok",
            "generated_at": v1.now_iso(),
            "event": {
                "id": event["id"],
                "lat": event.get("lat"),
                "lon": event.get("lon"),
                "burned_area_ha": event.get("burned_area_ha"),
                "last_detection": event.get("last_detection"),
                "perimeter_source": perimeter_source,
                "episode": episode,
            },
            "fire_source": {**fire_meta, "selected_seed_count": len(seeds)},
            "ifs": {
                "run_creation_time": v1.val.iso(v1.val.millis_to_dt(creation_ms)),
                "levels": list(v1.val.LEVELS),
                "transport_diagnostics": transport_diag,
            },
            "tuning_rows": all_rows,
        }
        v1.dump(out / f"{args.event_id}.json", doc)
        print(json.dumps({"status": "ok", "event_id": args.event_id, "rows": len(all_rows)}, indent=2))
    except Exception as exc:
        doc = {
            "schema_version": "4.0",
            "status": "excluded",
            "generated_at": v1.now_iso(),
            "event": {"id": args.event_id},
            "reason": f"{type(exc).__name__}: {str(exc)[:1200]}",
        }
        v1.dump(out / f"{args.event_id}.json", doc)
        print(json.dumps(doc, indent=2))
    return 0


def config_summary(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        if doc.get("status") != "ok":
            continue
        eid = str((doc.get("event") or {}).get("id") or "")
        for row in doc.get("tuning_rows") or []:
            if row.get("family") == "model":
                by[str(row["config_id"])].append({"event_id": eid, **row})

    out = []
    for cid, rows in by.items():
        first = rows[0]
        out.append({
            "config_id": cid,
            "height_mode": first.get("height_mode"),
            "radius_km": first.get("radius_km"),
            "horizon_h": first.get("horizon_h"),
            "events_n": len(rows),
            "csi80": v1.dist([r.get("csi80") for r in rows]),
            "precision80": v1.dist([r.get("precision80") for r in rows]),
            "recall80": v1.dist([r.get("recall80") for r in rows]),
            "pm25_enrichment_ratio": v1.dist([r.get("pm25_enrichment_ratio") for r in rows]),
            "geometric_area_km2": v1.dist([r.get("geometric_area_km2") for r in rows]),
            "cams_valid_area_km2": v1.dist([r.get("predicted_area_km2") for r in rows]),
        })
    return out


def pareto_select(docs: list[dict[str, Any]], area_weight: float = AREA_WEIGHT):
    summaries = config_summary(docs)
    if not summaries:
        raise RuntimeError("No model configurations available for Pareto selection")

    max_n = max(int(x["events_n"]) for x in summaries)
    support_floor = max(1, math.ceil(max_n * MIN_SUPPORT_FRACTION))
    supported = [
        x for x in summaries
        if int(x["events_n"]) >= support_floor
        and x["csi80"].get("median") is not None
        and x["geometric_area_km2"].get("median") is not None
        and float(x["geometric_area_km2"]["median"]) > 0
    ]
    if not supported:
        raise RuntimeError("No sufficiently supported model configuration")

    def csi(x): return float(x["csi80"]["median"])
    def area(x): return float(x["geometric_area_km2"]["median"])
    def precision(x): return float(x["precision80"].get("median") or -1.0)
    def enrichment(x): return float(x["pm25_enrichment_ratio"].get("median") or -1.0)

    pure = max(supported, key=lambda x: (csi(x), precision(x), enrichment(x), -area(x), x["config_id"]))

    front = []
    for x in supported:
        dominated = False
        for y in supported:
            if y is x:
                continue
            if area(y) <= area(x) + 1e-12 and csi(y) >= csi(x) - 1e-12:
                if area(y) < area(x) - 1e-12 or csi(y) > csi(x) + 1e-12:
                    dominated = True
                    break
        if not dominated:
            front.append(x)
    front.sort(key=lambda x: (area(x), -csi(x), x["config_id"]))

    log_areas = [math.log(max(area(x), 1e-9)) for x in front]
    csis = [csi(x) for x in front]
    amin, amax = min(log_areas), max(log_areas)
    cmin, cmax = min(csis), max(csis)
    complexity = {"100m": 1, "925hPa": 1, "850hPa": 1, "all3": 3}

    scored = []
    for x, la, cv in zip(front, log_areas, csis):
        an = 0.0 if amax == amin else (la - amin) / (amax - amin)
        cn = 1.0 if cmax == cmin else (cv - cmin) / (cmax - cmin)
        distance = math.sqrt(area_weight * an * an + (1.0 - area_weight) * (1.0 - cn) ** 2)
        scored.append((distance, x, an, cn))

    scored.sort(key=lambda t: (
        t[0],
        -precision(t[1]),
        -enrichment(t[1]),
        int(t[1]["horizon_h"]),
        float(t[1]["radius_km"]),
        complexity.get(str(t[1].get("height_mode")), 9),
        str(t[1]["config_id"]),
    ))
    distance, selected, an, cn = scored[0]

    sensitivity = {}
    for w in (0.33, 0.50, 0.67):
        tmp = []
        for _, x, ax, cx in scored:
            d = math.sqrt(w * ax * ax + (1.0 - w) * (1.0 - cx) ** 2)
            tmp.append((d, x))
        tmp.sort(key=lambda t: (
            t[0], -precision(t[1]), -enrichment(t[1]), int(t[1]["horizon_h"]),
            float(t[1]["radius_km"]), complexity.get(str(t[1].get("height_mode")), 9),
            str(t[1]["config_id"]),
        ))
        sensitivity[f"area_weight_{w:.2f}"] = tmp[0][1]["config_id"]

    selected_area = area(selected)
    pure_area = area(pure)
    meta = {
        "selection_rule": (
            "Among configurations with >=90% event support, construct the non-dominated frontier "
            "for higher tuning median CSI80 and lower tuning median geometric area. Normalize CSI "
            "and log(area) within that frontier and choose the point nearest the ideal (max CSI, "
            "min area) with equal 0.5/0.5 weights. Test/held-block outcomes never enter selection."
        ),
        "support_floor_events": support_floor,
        "supported_config_count": len(supported),
        "pareto_front_count": len(front),
        "pareto_front": [
            {
                "config_id": x["config_id"],
                "median_csi80": csi(x),
                "median_geometric_area_km2": area(x),
            }
            for x in front
        ],
        "selected_config_id": selected["config_id"],
        "selected_ideal_distance": round(distance, 6),
        "selected_normalized_log_area": round(an, 6),
        "selected_normalized_csi": round(cn, 6),
        "pure_csi_best_config_id": pure["config_id"],
        "pure_csi_best_median_csi80": csi(pure),
        "pure_csi_best_median_area_km2": pure_area,
        "selected_median_csi80": csi(selected),
        "selected_median_area_km2": selected_area,
        "training_csi_retention_vs_pure": round(csi(selected) / csi(pure), 4) if csi(pure) > 0 else None,
        "training_area_reduction_vs_pure": round(1.0 - selected_area / pure_area, 4) if pure_area > 0 else None,
        "weight_sensitivity": sensitivity,
    }
    return selected, meta


def row_for(doc: dict[str, Any], config_id: str, family: str):
    for row in doc.get("tuning_rows") or []:
        if family == "model" and row.get("family") == "model" and str(row.get("config_id")) == config_id:
            return row
        if (
            family == "matched_baseline"
            and row.get("family") == "matched_baseline"
            and str(row.get("matched_to_config_id")) == config_id
        ):
            return row
    return None


METRICS = ("pm25_enrichment_ratio", "precision80", "recall80", "csi80")


def med(values):
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(vals) if vals else None


def block_bootstrap(block_rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    diffs = []
    for b in block_rows:
        m = (b.get("model") or {}).get(metric)
        z = (b.get("matched_baseline") or {}).get(metric)
        if m is not None and z is not None:
            diffs.append(float(m) - float(z))
    if not diffs:
        return {"n_blocks": 0, "median_difference": None, "bootstrap_95ci": None}
    rng = random.Random(20260915)
    boots = [
        statistics.median([diffs[rng.randrange(len(diffs))] for _ in diffs])
        for _ in range(BOOTSTRAP_N)
    ]
    return {
        "n_blocks": len(diffs),
        "median_difference": round(statistics.median(diffs), 4),
        "bootstrap_95ci": [
            round(v1.quantile(boots, 0.025), 4),
            round(v1.quantile(boots, 0.975), 4),
        ],
    }


def nested_evaluate(args: argparse.Namespace) -> int:
    discovery = v1.load(Path(args.discovery_json))
    docs = v1.read_jsons(Path(args.grid_dir))
    by_doc = {
        str((d.get("event") or {}).get("id")): d
        for d in docs
        if d.get("status") == "ok" and (d.get("event") or {}).get("id")
    }
    excluded_grid = [
        {"event_id": str((d.get("event") or {}).get("id")), "reason": d.get("reason")}
        for d in docs if d.get("status") != "ok"
    ]

    events_meta = {str(e["event_id"]): e for e in discovery.get("events") or []}
    blocks = discovery.get("spatial_blocks") or []
    folds = []
    event_eval_rows = []

    for block in blocks:
        bid = str(block["block_id"])
        held_ids = [str(x) for x in block["event_ids"]]
        held_events = []
        train_events = []
        for eid, e in events_meta.items():
            row = {
                "id": eid,
                "lat": e.get("lat"),
                "lon": e.get("lon"),
                "last_detection": e.get("last_detection"),
            }
            (held_events if eid in held_ids else train_events).append(row)

        kept_train, embargoed, embargo_meta = v3.embargo_tuning(train_events, held_events)
        kept_ids = {str(e["id"]) for e in kept_train}
        train_docs = [by_doc[eid] for eid in sorted(kept_ids) if eid in by_doc]
        selected, selection_meta = pareto_select(train_docs, AREA_WEIGHT)
        cid = str(selected["config_id"])

        held_rows = []
        for eid in held_ids:
            doc = by_doc.get(eid)
            if not doc:
                continue
            mr = row_for(doc, cid, "model")
            br = row_for(doc, cid, "matched_baseline")
            if not mr or not br:
                continue
            eval_row = {
                "event_id": eid,
                "spatial_block_id": bid,
                "selected_config_id": cid,
                "model": {k: mr.get(k) for k in METRICS},
                "matched_baseline": {k: br.get(k) for k in METRICS},
                "model_geometric_area_km2": mr.get("geometric_area_km2"),
                "baseline_geometric_area_km2": br.get("geometric_area_km2"),
                "baseline_matched_radius_km": br.get("radius_km"),
                "area_match_error_fraction": br.get("area_match_error_fraction"),
            }
            held_rows.append(eval_row)
            event_eval_rows.append(eval_row)

        block_row = {
            "spatial_block_id": bid,
            "held_event_count": len(held_ids),
            "held_events_ok": len(held_rows),
            "selected_config_id": cid,
            "selection": selection_meta,
            "embargo": embargo_meta,
            "embargo_excluded_count": len(embargoed),
            "embargo_excluded": embargoed,
            "model": {k: med([(x.get("model") or {}).get(k) for x in held_rows]) for k in METRICS},
            "matched_baseline": {
                k: med([(x.get("matched_baseline") or {}).get(k) for x in held_rows]) for k in METRICS
            },
            "model_geometric_area_km2": med([x.get("model_geometric_area_km2") for x in held_rows]),
            "baseline_geometric_area_km2": med([x.get("baseline_geometric_area_km2") for x in held_rows]),
            "area_match_error_fraction": med([x.get("area_match_error_fraction") for x in held_rows]),
        }
        folds.append(block_row)

    ok_folds = [f for f in folds if int(f.get("held_events_ok") or 0) > 0]
    config_frequency = Counter(str(f["selected_config_id"]) for f in ok_folds)

    def block_dist(which: str, metric: str):
        return v1.dist([(f.get(which) or {}).get(metric) for f in ok_folds])

    event_model_area = [x.get("model_geometric_area_km2") for x in event_eval_rows]
    event_base_area = [x.get("baseline_geometric_area_km2") for x in event_eval_rows]
    area_ratio = [
        float(a) / float(b)
        for a, b in zip(event_model_area, event_base_area)
        if a is not None and b not in (None, 0)
    ]

    doc = {
        "schema_version": "4.0",
        "status": "nested_spatial_block_cv_complete" if len(ok_folds) >= 12 else "nested_spatial_block_cv_partial",
        "generated_at": v1.now_iso(),
        "evaluation_mode": "nested_leave_one_spatial_block_out",
        "outer_block_count": len(blocks),
        "outer_blocks_ok": len(ok_folds),
        "grid_events_ok": len(by_doc),
        "grid_events_excluded": excluded_grid,
        "fresh_eligible_preserved_unscored_count": discovery.get("fresh_eligible_preserved_unscored_count"),
        "selection_definition": {
            "primary_area_weight": AREA_WEIGHT,
            "minimum_support_fraction": MIN_SUPPORT_FRACTION,
            "area_axis": "event-median geodesic geometric footprint area; log transformed before normalization",
            "performance_axis": "event-median CAMS PM2.5 local-80th-percentile CSI",
            "baseline": "event-specific isotropic union of equal-radius fire-seed buffers, radius solved to match the selected model geometric area",
        },
        "selected_config_frequency_by_outer_block": dict(sorted(config_frequency.items())),
        "training_compactness_effect_across_folds": {
            "csi_retention_vs_pure_csi_best": v1.dist([
                (f.get("selection") or {}).get("training_csi_retention_vs_pure") for f in ok_folds
            ]),
            "area_reduction_vs_pure_csi_best": v1.dist([
                (f.get("selection") or {}).get("training_area_reduction_vs_pure") for f in ok_folds
            ]),
        },
        "block_weighted_model": {k: block_dist("model", k) for k in METRICS},
        "block_weighted_area_matched_baseline": {
            k: block_dist("matched_baseline", k) for k in METRICS
        },
        "block_weighted_model_minus_area_matched_baseline": {
            k: block_bootstrap(ok_folds, k) for k in METRICS
        },
        "event_level_supplement": {
            "model": {k: v1.dist([(x.get("model") or {}).get(k) for x in event_eval_rows]) for k in METRICS},
            "matched_baseline": {
                k: v1.dist([(x.get("matched_baseline") or {}).get(k) for x in event_eval_rows]) for k in METRICS
            },
            "selected_model_geometric_area_km2": v1.dist(event_model_area),
            "area_matched_baseline_geometric_area_km2": v1.dist(event_base_area),
            "model_to_baseline_area_ratio": v1.dist(area_ratio),
            "area_match_error_fraction": v1.dist([x.get("area_match_error_fraction") for x in event_eval_rows]),
        },
        "outer_folds": folds,
        "event_evaluations": event_eval_rows,
        "guardrail": (
            "Primary uncertainty is block-weighted. Each held spatial block is excluded before parameter selection. "
            "The baseline is area-matched, so CSI/precision/recall gains cannot be attributed simply to a larger footprint. "
            "CAMS remains semi-independent model evidence, not observation-only truth. Fresh post-v3 events were not scored."
        ),
    }
    out = Path(args.output_dir)
    v1.dump(out / "benchmark_summary.json", doc)
    print(json.dumps(doc, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)

    q = sub.add_parser("discover")
    q.add_argument("--target", type=int, default=120)
    q.add_argument("--output-dir", required=True)
    q.set_defaults(func=discover)

    q = sub.add_parser("grid-event")
    q.add_argument("--event-id", required=True)
    q.add_argument("--output-dir", required=True)
    q.set_defaults(func=grid_event)

    q = sub.add_parser("nested-evaluate")
    q.add_argument("--discovery-json", required=True)
    q.add_argument("--grid-dir", required=True)
    q.add_argument("--output-dir", required=True)
    q.set_defaults(func=nested_evaluate)

    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(int(args.func(args)))
