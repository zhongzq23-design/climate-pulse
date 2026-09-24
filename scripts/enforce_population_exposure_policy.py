#!/usr/bin/env python3
"""Enforce canonical population-exposure geometry semantics in daily ledgers.

This report-integrity gate deliberately re-evaluates persisted geometry semantics
so historical ledgers cannot keep an obsolete permissive grade. Ambiguous geometry
fails closed to context-grade.

Population Exposure Standard v1.2 applies hazard-specific admission gates:
- tropical-cyclone geometry needs an explicit supported wind threshold plus
  observed/actual temporal semantics;
- wildfire geometry needs verified GDACS/GWIS burned-area provenance, same-episode
  alignment, and coarse mapped/source burned-area consistency.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.geometry import shape

from wildfire_footprint_qc import assess_wildfire_footprint, strict_wildfire_qc_pass

ROOT = Path(__file__).resolve().parents[1]
DAILY_ROOT = ROOT / "data" / "history" / "daily"


def _method(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def strict_tc_wind_method(method: Any) -> tuple[bool, str | None]:
    """Return whether a TC footprint is eligible for primary exposure."""
    text = _method(method)
    if not text:
        return False, None
    if any(token in text for token in ("forecast", "fallback", "proximity", "generic", "unverified", "screening")):
        return False, None
    if "wind" not in text or "polygon" not in text:
        return False, None
    if not any(token in text for token in ("observed", "actual")):
        return False, None

    threshold = None
    if re.search(r"\b34\s*(?:kt|kts|knot|knots)\b", text):
        threshold = "34 kt"
    elif re.search(r"\b64\s*(?:kt|kts|knot|knots)\b", text):
        threshold = "64 kt"
    return threshold is not None, threshold


def canonical_geometry_semantics(
    event_type: str,
    footprint_method: Any,
    geometry_qc: Any = None,
) -> dict[str, Any]:
    """Return the canonical reporting grade for one mapped geometry."""
    typ = str(event_type or "").strip()
    method = _method(footprint_method)

    if typ == "Wildfire":
        if strict_wildfire_qc_pass(geometry_qc):
            return {
                "grade": "exposure_grade",
                "role": "burned_fire_perimeter",
                "purpose": "cross_hazard_population_and_burned_area",
                "suitable_for_cross_hazard_population": True,
                "interpretation": "Episode-aligned GDACS/GWIS wildfire burned-area/perimeter geometry that passed source and area-integrity QC.",
                "limitation": "A mapped perimeter is an exposure footprint, not proof of individual harm; the area-ratio check is an integrity guard, not an uncertainty interval.",
            }
        return {
            "grade": "context_grade",
            "role": "wildfire_geometry_pending_perimeter_validation",
            "purpose": "wildfire_event_context",
            "suitable_for_cross_hazard_population": False,
            "interpretation": "Wildfire mapped geometry retained for event context but not admitted to the primary exposure headline.",
            "limitation": "Exposure-grade wildfire admission requires verified GDACS/GWIS burned-area provenance, same-episode alignment and coarse mapped/source area consistency.",
        }

    if typ == "Storm":
        eligible, threshold = strict_tc_wind_method(method)
        if eligible:
            return {
                "grade": "exposure_grade",
                "role": "tropical_cyclone_observed_wind_zone",
                "purpose": "cross_hazard_population",
                "suitable_for_cross_hazard_population": True,
                "interpretation": f"Mapped observed/actual tropical-cyclone wind area with an explicit {threshold} threshold.",
                "limitation": "The mapped wind zone represents spatial exposure, not observed harm or verified individual presence.",
            }
        return {
            "grade": "context_grade",
            "role": "tropical_cyclone_wind_context_unverified",
            "purpose": "tc_wind_context_pending_threshold_temporal_validation",
            "suitable_for_cross_hazard_population": False,
            "interpretation": "Tropical-cyclone mapped geometry retained for event context but not admitted to the primary cross-hazard exposure headline.",
            "limitation": "The footprint does not explicitly prove both a supported wind threshold (34 kt or 64 kt) and observed/actual temporal semantics; generic wind, forecast, fallback and unverified geometry fail closed.",
        }

    if typ == "Flood":
        return {
            "grade": "context_grade",
            "role": "reported_flood_event_area_context",
            "purpose": "reported_area_population_context",
            "suitable_for_cross_hazard_population": False,
            "interpretation": "Center-aligned GDACS reported flood event / affected-area context polygon.",
            "limitation": "Spatial QC selects a plausible source context feature but does not establish observed or modelled inundation extent.",
        }

    if typ == "Drought":
        return {
            "grade": "risk_grade",
            "role": "drought_risk_impact_area",
            "purpose": "drought_risk_and_crop_context",
            "suitable_for_cross_hazard_population": False,
            "interpretation": "Mapped drought risk / potential-impact area.",
            "limitation": "The polygon represents drought risk/impact context, not confirmed human or crop damage.",
        }

    return {
        "grade": "context_grade",
        "role": "mapped_event_context",
        "purpose": "event_area_context",
        "suitable_for_cross_hazard_population": False,
        "interpretation": "Mapped event context with no approved primary-exposure semantics.",
        "limitation": "Ambiguous geometry fails closed and is excluded from the cross-hazard exposure headline.",
    }


def _retrofit_wildfire_qc(rec: dict[str, Any], fp: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct wildfire admission QC for historical daily ledgers."""
    try:
        geom = shape(fp.get("geometry"))
        if not geom.is_valid:
            geom = make_valid(geom)
    except Exception:  # noqa: BLE001
        geom = None

    latest = rec.get("raw_metrics_latest") if isinstance(rec.get("raw_metrics_latest"), dict) else {}
    maximum = rec.get("raw_metrics_max") if isinstance(rec.get("raw_metrics_max"), dict) else {}
    burned_area = latest.get("burned_area_ha")
    if burned_area is None:
        burned_area = maximum.get("burned_area_ha")
    provenance = rec.get("metric_provenance") if isinstance(rec.get("metric_provenance"), dict) else {}
    burned_prov = provenance.get("burned_area_ha") if isinstance(provenance.get("burned_area_ha"), dict) else None
    return assess_wildfire_footprint(
        geom,
        source_burned_area_ha=burned_area,
        footprint_episode_id=fp.get("gdacs_episode_id"),
        burned_area_provenance=burned_prov,
    )


def enforce_document(doc: dict[str, Any]) -> int:
    """Update one daily-ledger document in memory and return changed-record count."""
    changed = 0
    events = doc.get("events") if isinstance(doc.get("events"), dict) else {}
    for rec in events.values():
        if not isinstance(rec, dict):
            continue
        fp = rec.get("reporting_footprint") if isinstance(rec.get("reporting_footprint"), dict) else None
        if not fp or not isinstance(fp.get("geometry"), dict):
            continue

        before = json.dumps(fp, ensure_ascii=False, sort_keys=True)
        typ = str(rec.get("type") or "")
        if typ == "Wildfire":
            qc = fp.get("geometry_qc")
            if not strict_wildfire_qc_pass(qc):
                qc = _retrofit_wildfire_qc(rec, fp)
                fp["geometry_qc"] = qc
            if strict_wildfire_qc_pass(qc):
                fp["footprint_method"] = "gdacs_gwis_episode_aligned_burned_area_perimeter_polygon"
            elif not fp.get("footprint_method"):
                fp["footprint_method"] = "gdacs_wildfire_event_polygon_unverified"

        canonical = canonical_geometry_semantics(
            typ,
            fp.get("footprint_method"),
            fp.get("geometry_qc"),
        )
        fp["geometry_semantics"] = canonical
        after = json.dumps(fp, ensure_ascii=False, sort_keys=True)
        if before != after:
            changed += 1
    return changed


def main() -> None:
    files_changed = 0
    records_changed = 0
    if not DAILY_ROOT.exists():
        print("Population-exposure policy gate: no daily ledger directory; nothing to enforce.")
        return

    for path in sorted(DAILY_ROOT.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot validate daily ledger {path}: {exc}") from exc
        if not isinstance(doc, dict):
            raise RuntimeError(f"Daily ledger must be a JSON object: {path}")
        n = enforce_document(doc)
        if n:
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            files_changed += 1
            records_changed += n

    print(
        "Population-exposure policy gate: "
        f"updated {records_changed} mapped record(s) in {files_changed} ledger file(s)."
    )


if __name__ == "__main__":
    main()
