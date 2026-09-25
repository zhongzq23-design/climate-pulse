#!/usr/bin/env python3
"""Prospective, outcome-blind wildfire transport validation.

Phase 1 freezes predictions from a fresh production snapshot before satellite
outcomes are inspected. Phase 2 validates only already-frozen predictions.
Development parameters are never retuned here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import mapping, shape

import probe_wildfire_downwind as p
import probe_wildfire_downwind_satellite_validation as sat
import probe_wildfire_downwind_v2 as pv2
import probe_wildfire_downwind_v3 as pv3
import wildfire_transport_score_v1 as score

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data/reference/wildfire_transport_score_v1/locked_config.json"
SNAPSHOT = ROOT / "data/events/latest.json"
DEFAULT_FRESHNESS_HOURS = 48.0
DEFAULT_CANDIDATE_LIMIT = 36


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def locked_cfg(locked):
    params = locked.get("parameters") or {}
    required = ("sigma_km", "travel_tau_h", "frp_weighting", "competitor_masking")
    missing = [name for name in required if name not in params]
    if missing:
        raise RuntimeError(f"locked config missing parameters: {missing}")
    return {
        "config_id": locked["locked_config_id"],
        "sigma_km": params["sigma_km"],
        "travel_tau_h": params["travel_tau_h"],
        "frp_weighting": params["frp_weighting"],
        "competitor_masking": params["competitor_masking"],
    }


def select_current(snapshot, now, min_area, freshness_hours, candidate_limit):
    vals = snapshot.get("canonical_events") or snapshot.get("events") or []
    cutoff = now - timedelta(hours=float(freshness_hours))
    out = []
    for e in vals:
        if not isinstance(e, dict) or e.get("type") != "Wildfire":
            continue
        fp = e.get("footprint") if isinstance(e.get("footprint"), dict) else {}
        qc = e.get("footprint_qc") if isinstance(e.get("footprint_qc"), dict) else {}
        try:
            area = float(e.get("burned_area_ha") or 0)
            float(e["lat"])
            float(e["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        last = p.parse_dt(e.get("last_detection") or e.get("event_end"))
        if (
            area < float(min_area)
            or fp.get("status") != "ready"
            or qc.get("status") != "pass"
            or last is None
            or last < cutoff
        ):
            continue
        out.append(e)
    out.sort(
        key=lambda x: (float(x.get("burned_area_ha") or 0), str(x.get("id"))),
        reverse=True,
    )
    return out[: int(candidate_limit)]


def build_prediction(event, perimeter, seeds, tracks, leads, locked, reference_time):
    baseline = p.corridor_geometry(tracks, event, 50.0)
    if baseline.is_empty or not baseline.is_valid:
        raise RuntimeError("V4b baseline geometry is empty or invalid")
    cfg = locked_cfg(locked)
    scored, score_meta = score.score_geometry(
        event,
        seeds,
        tracks,
        leads,
        baseline,
        cfg,
        reference_time,
        competitor_wgs=None,
    )
    if scored.is_empty or not scored.is_valid:
        raise RuntimeError("locked transport-score geometry is empty or invalid")
    return baseline, scored, score_meta


def freeze(args):
    snapshot = load(SNAPSHOT)
    locked = load(LOCK)
    now = datetime.now(timezone.utc)
    candidates = select_current(
        snapshot,
        now,
        args.min_area_ha,
        args.freshness_hours,
        args.candidate_limit,
    )
    if not candidates:
        raise SystemExit("no fresh QC-passed wildfire candidates")

    ee, _project = p.initialize_ee()
    run, creation_ms, base_ms, leads, target_h = sat.choose_ifs_run(ee, now, 12)
    records = []
    frozen_count = 0
    attempted = 0

    for event in candidates:
        if frozen_count >= int(args.max_events):
            break
        attempted += 1
        stage = "perimeter"
        try:
            perimeter, source, episode = pv2.load_event_perimeter_stored_first(event)
            if perimeter is None or perimeter.is_empty or not perimeter.is_valid:
                raise RuntimeError("stored wildfire perimeter is empty or invalid")

            stage = "viirs_seeds"
            seeds, seed_meta = pv3.collect_fire_seeds_footprint(
                ee,
                event,
                perimeter,
                now,
                250.0,
                12,
            )

            stage = "ifs_transport"
            tracks, wind = sat.simulate_100m(ee, run, leads, seeds)

            stage = "geometry"
            baseline, scored, score_meta = build_prediction(
                event,
                perimeter,
                seeds,
                tracks,
                leads,
                locked,
                now,
            )

            stage = "freeze_hash"
            payload = {
                "event": {
                    "id": event["id"],
                    "lat": event["lat"],
                    "lon": event["lon"],
                    "burned_area_ha": event.get("burned_area_ha"),
                    "last_detection": event.get("last_detection"),
                },
                "frozen_at": p.iso(now),
                "source_snapshot_generated_at": snapshot.get("generated_at"),
                "perimeter_source": source,
                "episode": episode,
                "seeds": seeds,
                "seed_meta": seed_meta,
                "ifs": {
                    "creation_time": p.millis_to_iso(creation_ms),
                    "base_time": p.millis_to_iso(base_ms),
                    "leads_used_hours": leads,
                    "target_hours": target_h,
                    "wind_stats": wind,
                },
                "baseline": {
                    "family": "V4b",
                    "wind": "IFS 100 m",
                    "max_horizon_h": 12,
                    "buffer_km": 50,
                    "geometry": mapping(baseline),
                },
                "transport_score": {
                    "lock": locked,
                    "geometry": mapping(scored),
                    "geometry_meta": score_meta,
                },
                "guardrail": (
                    "Outcome-blind prospective prediction freeze; no satellite outcome "
                    "inspected and no retuning authorized."
                ),
            }
            payload["prediction_sha256"] = digest(payload)
            records.append(payload)
            frozen_count += 1
            print(
                "PROSPECTIVE_FROZEN "
                + json.dumps(
                    {
                        "event_id": event.get("id"),
                        "prediction_sha256": payload["prediction_sha256"],
                        "seed_count": len(seeds),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {str(exc)[:1200]}"
            records.append(
                {
                    "event": {"id": event.get("id")},
                    "status": "excluded",
                    "stage": stage,
                    "reason": reason,
                }
            )
            print(
                "PROSPECTIVE_EXCLUDED "
                + json.dumps(
                    {"event_id": event.get("id"), "stage": stage, "reason": reason},
                    separators=(",", ":"),
                ),
                flush=True,
            )

    doc = {
        "schema_version": "2.1",
        "experiment": "WILDFIRE_TRANSPORT_PROSPECTIVE_V2",
        "phase": "prediction_freeze",
        "frozen_at": p.iso(now),
        "source_snapshot_generated_at": snapshot.get("generated_at"),
        "freshness_hours": float(args.freshness_hours),
        "candidate_count": len(candidates),
        "candidate_limit": int(args.candidate_limit),
        "target_frozen_events": int(args.max_events),
        "attempted_events": attempted,
        "records": records,
    }
    dump(args.output, doc)
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "attempted": attempted,
                "target": int(args.max_events),
                "frozen": frozen_count,
                "excluded": sum(x.get("status") == "excluded" for x in records),
                "output": args.output,
            },
            indent=2,
        )
    )
    return 0


def verify(args):
    doc = load(args.input)
    good = 0
    for row in doc.get("records") or []:
        expected = row.get("prediction_sha256")
        if not expected:
            continue
        copy = dict(row)
        copy.pop("prediction_sha256", None)
        if digest(copy) != expected:
            raise SystemExit(
                f"prediction hash mismatch: {(row.get('event') or {}).get('id')}"
            )
        good += 1
    if not good:
        raise SystemExit("no frozen predictions")
    print(f"PROSPECTIVE_FREEZE_VERIFY=PASS records={good}")
    return 0



def inspect_snapshot(args):
    snapshot_path = Path(args.snapshot)
    footprint_root = Path(args.footprint_root)
    snapshot = load(snapshot_path)
    now = datetime.now(timezone.utc)
    candidates = select_current(
        snapshot,
        now,
        args.min_area_ha,
        args.freshness_hours,
        args.candidate_limit,
    )
    problems = []
    rows = []
    for event in candidates:
        fp = event.get("footprint") if isinstance(event.get("footprint"), dict) else {}
        rel = str(fp.get("path") or "")
        path = footprint_root / rel
        status = "ok"
        reason = None
        try:
            if not rel:
                raise RuntimeError("missing footprint path")
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"footprint is missing, non-file, or symlink: {rel}")
            doc = load(path)
            geom = shape(doc.get("geometry"))
            if geom.is_empty or not geom.is_valid:
                raise RuntimeError("stored footprint geometry is empty or invalid")
        except Exception as exc:  # noqa: BLE001
            status = "invalid"
            reason = f"{type(exc).__name__}: {str(exc)[:500]}"
            problems.append({"event_id": event.get("id"), "reason": reason})
        rows.append(
            {
                "event_id": event.get("id"),
                "last_detection": event.get("last_detection"),
                "burned_area_ha": event.get("burned_area_ha"),
                "footprint": rel,
                "status": status,
                "reason": reason,
            }
        )
    print(
        "PROSPECTIVE_SNAPSHOT_INSPECT "
        + json.dumps(
            {
                "snapshot": str(snapshot_path),
                "generated_at": snapshot.get("generated_at"),
                "freshness_hours": float(args.freshness_hours),
                "candidate_count": len(candidates),
                "invalid_count": len(problems),
                "candidates": rows,
            },
            separators=(",", ":"),
        )
    )
    if problems:
        raise SystemExit(f"snapshot inspection failed for {len(problems)} candidate footprints")
    return 0


def selftest(_args):
    locked = load(LOCK)
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    event = {
        "id": "synthetic-wildfire",
        "type": "Wildfire",
        "lat": 0.0,
        "lon": 0.0,
        "burned_area_ha": 20000,
    }
    seeds = [
        {"lon": -0.02, "lat": 0.00, "frp_mw": 40.0, "acq_time": p.iso(now - timedelta(hours=2))},
        {"lon": 0.00, "lat": 0.01, "frp_mw": 60.0, "acq_time": p.iso(now - timedelta(hours=1))},
        {"lon": 0.02, "lat": -0.01, "frp_mw": 30.0, "acq_time": p.iso(now - timedelta(hours=3))},
    ]
    leads = [0, 6, 12]
    tracks = {
        "0": [[-0.02, 0.00], [0.10, 0.02], [0.23, 0.03]],
        "1": [[0.00, 0.01], [0.12, 0.03], [0.25, 0.04]],
        "2": [[0.02, -0.01], [0.14, 0.01], [0.27, 0.02]],
    }
    from shapely.geometry import Point

    perimeter = Point(0.0, 0.0).buffer(0.05)
    baseline, scored, meta = build_prediction(
        event, perimeter, seeds, tracks, leads, locked, now
    )
    payload = {
        "event": event,
        "baseline": mapping(baseline),
        "transport_score": {"geometry": mapping(scored), "geometry_meta": meta},
    }
    h = digest(payload)
    if not h or len(h) != 64:
        raise SystemExit("selftest hash failure")
    err = meta.get("area_match_error_fraction")
    if err is None or float(err) > 1e-4:
        raise SystemExit(f"selftest area-match failure: {err}")
    print(
        "PROSPECTIVE_SELFTEST=PASS "
        + json.dumps(
            {
                "hash": h,
                "baseline_area_km2": meta.get("baseline_geodesic_area_km2"),
                "score_area_km2": meta.get("selected_geodesic_area_km2"),
                "area_match_error_fraction": err,
            },
            separators=(",", ":"),
        )
    )
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("freeze")
    q.add_argument("--output", required=True)
    q.add_argument("--max-events", type=int, default=12)
    q.add_argument("--min-area-ha", type=float, default=10000)
    q.add_argument("--freshness-hours", type=float, default=DEFAULT_FRESHNESS_HOURS)
    q.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    q.set_defaults(func=freeze)

    q = sub.add_parser("verify")
    q.add_argument("--input", required=True)
    q.set_defaults(func=verify)

    q = sub.add_parser("inspect")
    q.add_argument("--snapshot", required=True)
    q.add_argument("--footprint-root", required=True)
    q.add_argument("--min-area-ha", type=float, default=10000)
    q.add_argument("--freshness-hours", type=float, default=DEFAULT_FRESHNESS_HOURS)
    q.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    q.set_defaults(func=inspect_snapshot)

    q = sub.add_parser("selftest")
    q.set_defaults(func=selftest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
