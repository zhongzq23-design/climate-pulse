#!/usr/bin/env python3
"""Geometry guards for periodic-report raster extraction.

Shapely unions can occasionally return a GeometryCollection after validity repair.
Rasterio's transform_geom expects polygon coordinates for these report rasters, so
strip non-polygonal remnants while preserving all Polygon/MultiPolygon parts.
"""
from __future__ import annotations

from typing import Any

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union


def polygonal_only(geom: Any):
    if geom is None or getattr(geom, "is_empty", True):
        return None
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(geom, Polygon):
        return geom
    if isinstance(geom, MultiPolygon):
        return geom
    parts = []
    if isinstance(geom, GeometryCollection) or hasattr(geom, "geoms"):
        for child in getattr(geom, "geoms", []):
            clean = polygonal_only(child)
            if clean is None:
                continue
            if isinstance(clean, Polygon):
                parts.append(clean)
            else:
                parts.extend(list(clean.geoms))
    if not parts:
        return None
    out = unary_union(parts)
    if not out.is_valid:
        out = make_valid(out)
    if isinstance(out, (Polygon, MultiPolygon)) and not out.is_empty:
        return out
    # A final validity repair can theoretically reintroduce a collection.
    if isinstance(out, GeometryCollection):
        again = []
        for child in out.geoms:
            if isinstance(child, Polygon):
                again.append(child)
            elif isinstance(child, MultiPolygon):
                again.extend(list(child.geoms))
        if again:
            final = unary_union(again)
            return final if isinstance(final, (Polygon, MultiPolygon)) and not final.is_empty else None
    return None
