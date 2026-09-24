#!/usr/bin/env python3
"""Wildfire footprint admission QC for Climate Pulse reporting.

A wildfire polygon is eligible for the cross-hazard mapped-population headline
only when the geometry is traceable to the same GDACS/GWIS episode as the
source-reported burned-area metric and the mapped area is broadly consistent
with that source area. The area comparison is an operational integrity check,
not a scientific uncertainty interval.
"""
from __future__ import annotations

import math
from typing import Any

from pyproj import Geod

GEOD = Geod(ellps="WGS84")
AREA_WARNING_REL_DIFF = 0.25
AREA_FAIL_RATIO_MIN = 0.5
AREA_FAIL_RATIO_MAX = 2.0


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def geodesic_area_ha(geom) -> float | None:
    """Return absolute geodesic polygon area in hectares."""
    if geom is None or geom.is_empty:
        return None
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(float(area_m2)) / 10_000.0
    except Exception:  # noqa: BLE001
        total = 0.0
        for part in getattr(geom, "geoms", []):
            try:
                area_m2, _ = GEOD.geometry_area_perimeter(part)
                total += abs(float(area_m2))
            except Exception:  # noqa: BLE001
                pass
        return total / 10_000.0 if total > 0 else None


def assess_wildfire_footprint(
    geom,
    *,
    source_burned_area_ha: Any,
    footprint_episode_id: Any,
    burned_area_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return fail-closed admission QC for one wildfire geometry.

    Admission requires:
    - source-reported burned area with GDACS/GWIS provenance;
    - the burned-area provenance episode to match the footprint episode;
    - a non-empty mapped polygon;
    - a coarse mapped/source area ratio between 0.5 and 2.0.

    A >25% area difference is retained as a warning while still passing the
    coarse integrity gate. The ratio bounds are corruption/semantic guards, not
    an uncertainty model and not a definition of burned area.
    """
    prov = burned_area_provenance if isinstance(burned_area_provenance, dict) else {}
    source = str(prov.get("source") or "").strip().lower()
    method = str(prov.get("method") or "").strip().lower()
    source_episode = prov.get("gdacs_episode_id")
    burned = _finite(source_burned_area_ha)
    mapped = geodesic_area_ha(geom)

    authority_ok = "gdacs" in source and "gwis" in source and "source_reported" in method
    try:
        episode_aligned = int(source_episode) == int(footprint_episode_id)
    except (TypeError, ValueError):
        episode_aligned = False

    ratio = None
    rel_diff = None
    area_ok = False
    if burned is not None and burned > 0 and mapped is not None and mapped > 0:
        ratio = mapped / burned
        rel_diff = abs(mapped - burned) / burned
        area_ok = AREA_FAIL_RATIO_MIN <= ratio <= AREA_FAIL_RATIO_MAX

    reasons: list[str] = []
    if not authority_ok:
        reasons.append("burned_area_source_provenance_not_verified_gdacs_gwis")
    if not episode_aligned:
        reasons.append("burned_area_and_footprint_episode_not_aligned")
    if burned is None or burned <= 0:
        reasons.append("source_burned_area_missing_or_invalid")
    if mapped is None or mapped <= 0:
        reasons.append("mapped_polygon_area_missing_or_invalid")
    elif ratio is not None and not area_ok:
        reasons.append("mapped_area_grossly_inconsistent_with_source_burned_area")

    passed = authority_ok and episode_aligned and area_ok
    out: dict[str, Any] = {
        "status": "pass" if passed else "failed",
        "reason": "episode_aligned_gdacs_gwis_burned_area_perimeter" if passed else ";".join(reasons),
        "semantic_role": "burned_area_perimeter" if passed else "wildfire_event_geometry_unverified",
        "exposure_grade_eligible": bool(passed),
        "source_authority_verified": bool(authority_ok),
        "episode_aligned": bool(episode_aligned),
        "footprint_episode_id": footprint_episode_id,
        "source_burned_area_episode_id": source_episode,
        "source_burned_area_ha": None if burned is None else round(burned, 2),
        "mapped_polygon_area_ha": None if mapped is None else round(mapped, 2),
        "mapped_to_source_area_ratio": None if ratio is None else round(ratio, 4),
        "relative_area_difference_pct": None if rel_diff is None else round(100.0 * rel_diff, 2),
        "area_integrity_rule": (
            f"{AREA_FAIL_RATIO_MIN} <= mapped_polygon_area/source_burned_area <= {AREA_FAIL_RATIO_MAX}; "
            "coarse integrity gate, not an uncertainty interval"
        ),
    }
    if passed and rel_diff is not None and rel_diff > AREA_WARNING_REL_DIFF:
        out["warning"] = "mapped_vs_source_burned_area_difference_gt_25pct"
    return out


def strict_wildfire_qc_pass(qc: Any) -> bool:
    return bool(
        isinstance(qc, dict)
        and qc.get("status") == "pass"
        and qc.get("semantic_role") == "burned_area_perimeter"
        and qc.get("exposure_grade_eligible") is True
        and qc.get("source_authority_verified") is True
        and qc.get("episode_aligned") is True
    )
