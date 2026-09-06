#!/usr/bin/env python3
"""Flood-footprint selection and QC helpers.

GDACS polygon responses can contain multiple polygon-like resources with different
roles. For a public flood footprint, Climate Pulse must not blindly union every
polygon returned by the endpoint. Instead, prefer the source polygon feature that
is spatially aligned with the reported event coordinate and fail closed when no
credible center-aligned polygon exists.

The returned geometry remains a GDACS source geometry. This module does not infer
inundation extent and does not manufacture a substitute polygon.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from pyproj import Geod
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, shape
from shapely.ops import nearest_points

GEOD = Geod(ellps="WGS84")
MAX_CENTER_DISTANCE_KM = 75.0


@dataclass
class FloodCandidate:
    index: int
    geometry: Any
    area_km2: float
    center_distance_km: float
    center_inside: bool
    metadata_text: str
    metadata_preferred: bool
    metadata_context_like: bool


def polygon_parts(g) -> list[Any]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if isinstance(g, GeometryCollection):
        out: list[Any] = []
        for part in g.geoms:
            out.extend(polygon_parts(part))
        return out
    return []


def polygonal_geometry(feature: dict[str, Any]):
    geom = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    try:
        g = shape(geom)
        if not g.is_valid:
            g = make_valid(g)
        parts = polygon_parts(g)
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return MultiPolygon(parts)
    except Exception:  # noqa: BLE001
        return None


def geodesic_area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(float(area_m2)) / 1_000_000.0
    except Exception:  # noqa: BLE001
        total = 0.0
        for part in polygon_parts(geom):
            try:
                area_m2, _ = GEOD.geometry_area_perimeter(part)
                total += abs(float(area_m2))
            except Exception:  # noqa: BLE001
                pass
        return total / 1_000_000.0


def distance_to_geometry_km(point: Point, geom) -> float:
    if geom is None or geom.is_empty:
        return math.inf
    try:
        if geom.covers(point):
            return 0.0
        _, q = nearest_points(point, geom)
        _, _, dist_m = GEOD.inv(float(point.x), float(point.y), float(q.x), float(q.y))
        return max(0.0, float(dist_m) / 1000.0)
    except Exception:  # noqa: BLE001
        return math.inf


def metadata_text(feature: dict[str, Any]) -> str:
    props = feature.get("properties") if isinstance(feature, dict) else None
    try:
        return json.dumps(props or {}, ensure_ascii=False, sort_keys=True).lower()
    except Exception:  # noqa: BLE001
        return str(props or "").lower()


def _metadata_flags(text: str) -> tuple[bool, bool]:
    preferred_terms = ("flood", "affected", "event polygon", "eventpolygon", "analysis", "aoi")
    context_terms = ("admin", "administrative", "country boundary", "boundary", "context", "basin")
    return any(x in text for x in preferred_terms), any(x in text for x in context_terms)


def candidates(features: list[dict[str, Any]], lon: float, lat: float) -> list[FloodCandidate]:
    point = Point(float(lon), float(lat))
    out: list[FloodCandidate] = []
    for i, feature in enumerate(features):
        g = polygonal_geometry(feature)
        if g is None or g.is_empty:
            continue
        text = metadata_text(feature)
        preferred, context_like = _metadata_flags(text)
        out.append(FloodCandidate(
            index=i,
            geometry=g,
            area_km2=geodesic_area_km2(g),
            center_distance_km=distance_to_geometry_km(point, g),
            center_inside=bool(g.covers(point)),
            metadata_text=text,
            metadata_preferred=preferred,
            metadata_context_like=context_like,
        ))
    return out


def _rank(c: FloodCandidate) -> tuple:
    """Lower tuple is better; spatial alignment dominates metadata."""
    if c.center_inside:
        spatial_tier = 0
    elif c.center_distance_km <= 10:
        spatial_tier = 1
    elif c.center_distance_km <= 30:
        spatial_tier = 2
    elif c.center_distance_km <= MAX_CENTER_DISTANCE_KM:
        spatial_tier = 3
    else:
        spatial_tier = 4
    return (
        spatial_tier,
        0 if c.metadata_preferred else 1,
        1 if c.metadata_context_like else 0,
        c.center_distance_km,
        c.area_km2,
        c.index,
    )


def select_flood_geometry(features: list[dict[str, Any]], lon: float, lat: float):
    """Return ``(geometry, qc)`` for a GDACS flood event.

    QC is intentionally fail-closed. A source polygon farther than 75 km from the
    reported event coordinate is not exposed as the event footprint and is not
    used for downstream GDP/population calculations.
    """
    cs = candidates(features, lon, lat)
    if not cs:
        return None, {
            "status": "failed",
            "reason": "no_polygon_feature",
            "candidate_polygon_features": 0,
            "selected_feature_index": None,
        }

    best = min(cs, key=_rank)
    if not math.isfinite(best.center_distance_km) or best.center_distance_km > MAX_CENTER_DISTANCE_KM:
        return None, {
            "status": "failed",
            "reason": "no_center_aligned_polygon",
            "candidate_polygon_features": len(cs),
            "selected_feature_index": None,
            "nearest_polygon_distance_km": None if not math.isfinite(best.center_distance_km) else round(best.center_distance_km, 1),
            "selection_policy": "fail closed when no GDACS polygon feature is within 75 km of the reported event coordinate",
        }

    # Prefer one complete source feature rather than unioning unrelated resources.
    # If the chosen feature itself is MultiPolygon, all of its source parts are
    # retained because they belong to the same GDACS feature.
    qc = {
        "status": "pass",
        "reason": "center_aligned_source_feature" if best.center_inside else "nearest_source_feature",
        "candidate_polygon_features": len(cs),
        "selected_feature_index": best.index,
        "discarded_polygon_features": max(0, len(cs) - 1),
        "reported_center_inside": best.center_inside,
        "reported_center_distance_km": round(best.center_distance_km, 2),
        "selected_area_km2": round(best.area_km2, 2),
        "selection_policy": "single GDACS polygon feature aligned to the reported event coordinate; unrelated polygon features are not unioned",
    }
    minx, miny, maxx, maxy = best.geometry.bounds
    qc["selected_bbox"] = [round(minx, 4), round(miny, 4), round(maxx, 4), round(maxy, 4)]
    if best.area_km2 > 300_000:
        qc["warning"] = "very_large_selected_area"
    return best.geometry, qc
