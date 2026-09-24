#!/usr/bin/env python3
"""Synchronize climate-context references and enforce public coverage.

The browser prefers ``canonical_events`` while backend products may also carry
an ``events`` view. This helper copies climate references across both arrays by
stable event ID and then fails closed unless every canonical event has either:

* ``status=ready`` with an existing per-event climate JSON file; or
* ``status=unavailable`` with an explicit reason.

A missing climate reference is a pipeline coverage error, not a user-facing
"wait for the next run" state.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
INDEX = ROOT / "data" / "climate" / "event_timeseries" / "index.json"


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def valid_public_ref(event_id: str, ref: Any) -> tuple[bool, str]:
    if not isinstance(ref, dict):
        return False, "missing climate_context"
    status = ref.get("status")
    if status == "ready":
        path = str(ref.get("path") or "").strip()
        if not path:
            return False, "ready without path"
        if not (ROOT / path).exists():
            return False, f"ready path does not exist: {path}"
        return True, "ready"
    if status == "unavailable":
        if not str(ref.get("reason") or "").strip():
            return False, "unavailable without reason"
        return True, "unavailable"
    return False, f"invalid status: {status!r}"


def main() -> None:
    snap = load(LATEST, {})
    if not isinstance(snap, dict):
        raise RuntimeError("latest.json is not a JSON object")

    refs: dict[str, dict[str, Any]] = {}

    for key in ("events", "canonical_events"):
        items = snap.get(key)
        if not isinstance(items, list):
            continue
        for event in items:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            ref = event.get("climate_context")
            if event_id and isinstance(ref, dict) and ref.get("status"):
                refs[event_id] = copy.deepcopy(ref)

    # The index is authoritative evidence that a ready per-event file exists.
    index = load(INDEX, {})
    entries = index.get("events") if isinstance(index, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            event_id = str(entry.get("event_id") or "")
            path = entry.get("path")
            if not event_id or not path:
                continue
            current = refs.get(event_id)
            if (
                not isinstance(current, dict)
                or current.get("status") != "ready"
                or not current.get("path")
            ):
                refs[event_id] = {"status": "ready", "path": path}

    updated = 0
    matched = 0
    for key in ("events", "canonical_events"):
        items = snap.get(key)
        if not isinstance(items, list):
            continue
        for event in items:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            ref = refs.get(event_id)
            if not event_id or not ref:
                continue
            matched += 1
            if event.get("climate_context") != ref:
                event["climate_context"] = copy.deepcopy(ref)
                updated += 1

    # Publication invariant: every canonical event must have an explicit final state.
    gaps: list[str] = []
    canonical = snap.get("canonical_events")
    canonical_count = 0
    ready_count = 0
    unavailable_count = 0
    if isinstance(canonical, list):
        for event in canonical:
            if not isinstance(event, dict):
                continue
            canonical_count += 1
            event_id = str(event.get("id") or "<missing-id>")
            ref = event.get("climate_context")
            ok, detail = valid_public_ref(event_id, ref)
            if not ok:
                gaps.append(f"{event_id}: {detail}")
            elif ref.get("status") == "ready":
                ready_count += 1
            else:
                unavailable_count += 1

    if gaps:
        preview = "; ".join(gaps[:20])
        raise RuntimeError(
            f"Climate-context publication coverage failed for {len(gaps)} canonical event(s): {preview}"
        )

    LATEST.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "ok",
        "reference_count": len(refs),
        "matched_event_views": matched,
        "updated_event_views": updated,
        "canonical_events": canonical_count,
        "canonical_ready": ready_count,
        "canonical_unavailable": unavailable_count,
        "coverage": "complete",
    }, indent=2))


if __name__ == "__main__":
    main()
