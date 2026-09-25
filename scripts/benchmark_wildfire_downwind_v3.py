#!/usr/bin/env python3
"""Spatial-block wildfire downwind benchmark (v3).

Research prototype only.

V3 replaces administrative-country grouping with purely geographic blocks.
Candidate fires are clustered by complete-link great-circle distance so every
pair of fires in one block is within a predeclared maximum diameter. Entire
spatial blocks are atomic between tuning and test. A pre-scoring
spatiotemporal embargo then removes nearby concurrent tuning fires around the
held-out blocks.

Model tuning/compactness selection is identical to v2 so that the effect of
changing the validation geography can be interpreted cleanly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import benchmark_wildfire_downwind as v1
import benchmark_wildfire_downwind_v2 as v2

LATEST = v1.LATEST
BENCHMARK_SALT = "climate-pulse-wildfire-benchmark-v3-spatial-blocks"
BLOCK_MAX_DIAMETER_KM = 500.0
MIN_TEST_BLOCKS = 3
MIN_TUNE_BLOCKS = 3
MIN_TUNING_EVENTS_AFTER_EMBARGO = 20
EMBARGO_CANDIDATES = ((500.0, 72.0), (350.0, 48.0), (250.0, 24.0), (0.0, 0.0))


def stable_hash(text: str) -> str:
    return hashlib.sha256(f"{BENCHMARK_SALT}|{text}".encode()).hexdigest()


def block_distance(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float:
    return max(v2.haversine_km(x, y) for x in a for y in b)


def internal_diameter(block: list[dict[str, Any]]) -> float:
    if len(block) < 2:
        return 0.0
    return max(v2.haversine_km(block[i], block[j]) for i in range(len(block)) for j in range(i + 1, len(block)))


def spherical_centroid(block: list[dict[str, Any]]) -> tuple[float, float]:
    xs = ys = zs = 0.0
    for e in block:
        lat, lon = math.radians(float(e["lat"])), math.radians(float(e["lon"]))
        xs += math.cos(lat) * math.cos(lon)
        ys += math.cos(lat) * math.sin(lon)
        zs += math.sin(lat)
    n = max(1, len(block))
    x, y, z = xs / n, ys / n, zs / n
    lon = math.atan2(y, x)
    lat = math.atan2(z, math.sqrt(x * x + y * y))
    return math.degrees(lat), math.degrees(lon)


def make_spatial_blocks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic complete-link clustering cut at BLOCK_MAX_DIAMETER_KM."""
    clusters: list[list[dict[str, Any]]] = [[e] for e in sorted(events, key=lambda x: str(x["id"]))]
    while True:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = block_distance(clusters[i], clusters[j])
                if d > BLOCK_MAX_DIAMETER_KM + 1e-9:
                    continue
                ids = sorted(str(e["id"]) for e in clusters[i] + clusters[j])
                key = (round(d, 9), stable_hash("|".join(ids)))
                if best is None or key < best[0]:
                    best = (key, i, j)
        if best is None:
            break
        _, i, j = best
        merged = clusters[i] + clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]

    out = []
    for block in clusters:
        ids = sorted(str(e["id"]) for e in block)
        lat, lon = spherical_centroid(block)
        out.append({
            "block_id": f"SB-{stable_hash('|'.join(ids))[:10]}",
            "event_count": len(block),
            "centroid_lat": round(lat, 4),
            "centroid_lon": round(lon, 4),
            "max_pairwise_distance_km": round(internal_diameter(block), 1),
            "event_ids": ids,
        })
    out.sort(key=lambda b: (-int(b["event_count"]), str(b["block_id"])))
    return out


def choose_test_blocks(blocks: list[dict[str, Any]], total_events: int) -> tuple[set[str], dict[str, Any]]:
    if len(blocks) < MIN_TEST_BLOCKS + MIN_TUNE_BLOCKS:
        raise RuntimeError(f"Need >= {MIN_TEST_BLOCKS + MIN_TUNE_BLOCKS} spatial blocks; found {len(blocks)}")
    target = max(1, round(total_events / 3))

    # Dynamic subset search. For each (event_count, block_count), retain one
    # deterministic representative subset; final selection is closest to the
    # target and then uses the fewest coherent test blocks.
    states: dict[tuple[int, int], tuple[str, ...]] = {(0, 0): tuple()}
    for block in blocks:
        bid, n = str(block["block_id"]), int(block["event_count"])
        updated = dict(states)
        for (count, k), combo in states.items():
            key = (count + n, k + 1)
            candidate = tuple(sorted(combo + (bid,)))
            incumbent = updated.get(key)
            if incumbent is None or stable_hash("|".join(candidate)) < stable_hash("|".join(incumbent)):
                updated[key] = candidate
        states = updated

    best = None
    best_combo = None
    for (count, k), combo in states.items():
        if k < MIN_TEST_BLOCKS or len(blocks) - k < MIN_TUNE_BLOCKS:
            continue
        if count <= 0 or count >= total_events:
            continue
        score = (abs(count - target), k, stable_hash("|".join(combo)))
        if best is None or score < best:
            best, best_combo = score, combo
    if best_combo is None:
        raise RuntimeError("Unable to construct spatial-block holdout")

    counts = {str(b["block_id"]): int(b["event_count"]) for b in blocks}
    return set(best_combo), {
        "target_test_events": target,
        "selected_test_block_ids": list(best_combo),
        "selected_test_event_count": sum(counts[x] for x in best_combo),
        "block_event_counts": counts,
        "selection_rule": "Spatial blocks are atomic. Choose a subset closest to one-third of events, requiring >=3 test and >=3 tuning blocks; among equally close subsets prefer fewer coherent test blocks, then deterministic hash tie-break.",
    }


def event_time(event: dict[str, Any]) -> datetime | None:
    return v2.event_time(event)


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
                        d_km = v2.haversine_km(event, held)
                        if d_km <= radius_km:
                            hit = {
                                "event_id": event.get("id"),
                                "near_test_event_id": held.get("id"),
                                "distance_km": round(d_km, 1),
                                "time_difference_h": round(delta_h, 1),
                            }
                            break
            if hit is None:
                kept.append(event)
            else:
                excluded.append(hit)
        if len(kept) >= MIN_TUNING_EVENTS_AFTER_EMBARGO or radius_km == 0:
            return kept, excluded, {
                "radius_km": radius_km,
                "time_window_hours": hours,
                "minimum_tuning_events": MIN_TUNING_EVENTS_AFTER_EMBARGO,
                "rule": "After spatial-block assignment, exclude a tuning fire when it is both spatially close and temporally concurrent with any held-out fire. Use the strongest predeclared embargo that retains at least 20 tuning events.",
            }
    raise RuntimeError("Embargo selection failed")


def discover(args: argparse.Namespace) -> int:
    rows = v1.candidates(v1.load(LATEST), args.target)
    blocks = make_spatial_blocks(rows)
    selected, split_meta = choose_test_blocks(blocks, len(rows))
    event_to_block = {eid: str(b["block_id"]) for b in blocks for eid in b["event_ids"]}

    test = [e for e in rows if event_to_block[str(e["id"])] in selected]
    tune_pre = [e for e in rows if event_to_block[str(e["id"])] not in selected]
    tune, embargoed, embargo_meta = embargo_tuning(tune_pre, test)

    tune_block_ids = sorted({event_to_block[str(e["id"])] for e in tune})
    test_block_ids = sorted({event_to_block[str(e["id"])] for e in test})
    overlap = sorted(set(tune_block_ids) & set(test_block_ids))
    if overlap:
        raise RuntimeError(f"Spatial-block leakage after split: {overlap}")

    def fmt(e: dict[str, Any]) -> dict[str, Any]:
        dt = event_time(e)
        return {
            "event_id": e["id"],
            "spatial_block_id": event_to_block[str(e["id"])],
            "lat": e.get("lat"), "lon": e.get("lon"),
            "event_time": dt.isoformat().replace("+00:00", "Z") if dt else None,
        }

    doc = {
        "schema_version": "3.0", "generated_at": v1.now_iso(), "target": args.target,
        "candidate_count": len(rows), "tune_count_pre_embargo": len(tune_pre), "tune_count": len(tune),
        "test_count": len(test), "embargo_excluded_count": len(embargoed),
        "eligibility_gate": "Wildfire; burned area >=10,000 ha; stored footprint exists; wildfire footprint QC pass.",
        "spatial_block_definition": {
            "method": "deterministic complete-link clustering on great-circle distance",
            "max_pairwise_diameter_km": BLOCK_MAX_DIAMETER_KM,
            "administrative_boundaries_used": False,
            "interpretation": "Every pair of event centroids inside a block is <=500 km apart. Blocks depend only on fire coordinates, not country labels.",
        },
        "spatial_blocks": blocks,
        "grouped_split": split_meta,
        "embargo": embargo_meta,
        "block_overlap_check": overlap,
        "tune_block_ids": tune_block_ids,
        "test_block_ids": test_block_ids,
        "embargo_excluded": embargoed,
        "tune_events": [fmt(e) for e in tune],
        "test_events": [fmt(e) for e in test],
    }
    out = Path(args.output_dir)
    v1.dump(out / "discovery.json", doc)
    v1.github_output("tune_matrix", json.dumps([{"event_id": e["id"]} for e in tune], separators=(",", ":")))
    v1.github_output("test_matrix", json.dumps([{"event_id": e["id"]} for e in test], separators=(",", ":")))
    v1.github_output("candidate_count", str(len(rows)))
    print(json.dumps(doc, indent=2))
    return 0


def summarize(args: argparse.Namespace) -> int:
    # Use the neutral v1 summary, then add V3 spatial-block metadata and
    # per-block diagnostics. v2 selection is reused unchanged.
    rc = v1.summarize(args)
    path = Path(args.output_dir) / "benchmark_summary.json"
    doc = v1.load(path)
    discovery = v1.load(Path(args.discovery_json))
    lockdoc = v1.load(Path(args.locked_json))
    test_docs = v1.read_jsons(Path(args.test_dir))
    event_to_block = {str(e["event_id"]): str(e["spatial_block_id"]) for e in discovery.get("test_events") or []}

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in test_docs:
        if d.get("status") != "ok":
            continue
        eid = str((d.get("event") or {}).get("id") or "")
        bid = event_to_block.get(eid)
        if bid:
            grouped[bid].append(d)

    per_block = {}
    for bid, rows in sorted(grouped.items()):
        per_block[bid] = {
            "n": len(rows),
            "pm25_enrichment_ratio": v1.dist([v1.metric_from_test(x, False, "enrichment") for x in rows]),
            "precision80": v1.dist([v1.metric_from_test(x, False, "precision80") for x in rows]),
            "recall80": v1.dist([v1.metric_from_test(x, False, "recall80") for x in rows]),
            "csi80": v1.dist([v1.metric_from_test(x, False, "csi80") for x in rows]),
            "baseline_csi80": v1.dist([v1.metric_from_test(x, True, "csi80") for x in rows]),
        }

    doc.update({
        "schema_version": "3.0",
        "benchmark_version": "spatial_block_compactness_v3",
        "spatial_block_definition": discovery.get("spatial_block_definition"),
        "spatial_blocks": discovery.get("spatial_blocks"),
        "grouped_split": discovery.get("grouped_split"),
        "embargo": discovery.get("embargo"),
        "embargo_excluded_count": discovery.get("embargo_excluded_count"),
        "tune_block_ids": discovery.get("tune_block_ids"),
        "test_block_ids": discovery.get("test_block_ids"),
        "block_overlap_check": discovery.get("block_overlap_check"),
        "selection_rule": lockdoc.get("selection_rule"),
        "regularization": lockdoc.get("regularization"),
        "selection_sensitivity": lockdoc.get("selection_sensitivity"),
        "out_of_sample_by_spatial_block": per_block,
        "interpretation_guardrail": "Validation groups are geographic fire-neighborhood blocks defined only by coordinates, not countries. Held-out blocks are absent from tuning and a pre-scoring spatiotemporal embargo removes nearby concurrent tuning fires. Population values are residents intersecting a screening corridor, not confirmed smoke exposure. CAMS is semi-independent rather than observation-only truth.",
    })
    v1.dump(path, doc)
    print(json.dumps(doc, indent=2))
    return rc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    q = sub.add_parser("discover"); q.add_argument("--target", type=int, default=45); q.add_argument("--output-dir", required=True); q.set_defaults(func=discover)
    q = sub.add_parser("tune-event"); q.add_argument("--event-id", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=v2.tune_event)
    q = sub.add_parser("select"); q.add_argument("--input-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=v2.select)
    q = sub.add_parser("test-event"); q.add_argument("--event-id", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=v2.test_event)
    q = sub.add_parser("summarize"); q.add_argument("--discovery-json", required=True); q.add_argument("--locked-json", required=True); q.add_argument("--tune-dir", required=True); q.add_argument("--test-dir", required=True); q.add_argument("--output-dir", required=True); q.set_defaults(func=summarize)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(int(args.func(args)))
