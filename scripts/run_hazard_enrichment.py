#!/usr/bin/env python3
"""Compatibility runner for hazard enrichment.

GDACS polygons can contain self-intersections or overlapping invalid rings. This
runner repairs individual geometries before unioning them and expands the GHSL
cache path correctly on GitHub-hosted runners.
"""
from __future__ import annotations

import os
from pathlib import Path

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import unary_union

import enrich_hazard_exposure as h


def polygon_parts(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if isinstance(g, GeometryCollection):
        out = []
        for x in g.geoms:
            out.extend(polygon_parts(x))
        return out
    return []


def safe_polygons_union(features, predicate=None):
    geoms = []
    for f in features:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        if predicate is not None and not predicate(f):
            continue
        try:
            g = shape(geom)
            if not g.is_valid:
                g = make_valid(g)
            for p in polygon_parts(g):
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty:
                    geoms.append(p)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN skipping invalid GDACS polygon: {type(exc).__name__}: {exc}")
    if not geoms:
        return None
    try:
        out = unary_union(geoms)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN unary_union failed; retrying repaired parts: {type(exc).__name__}: {exc}")
        repaired = []
        for g in geoms:
            try:
                repaired.extend(polygon_parts(make_valid(g)))
            except Exception:  # noqa: BLE001
                pass
        if not repaired:
            return None
        out = unary_union(repaired)
    if not out.is_valid:
        out = make_valid(out)
    parts = polygon_parts(out)
    if not parts:
        return None
    return unary_union(parts)


# Monkey-patch only the geometry normalization layer; the scientific rules stay
# in enrich_hazard_exposure.py.
h.polygons_union = safe_polygons_union
cache_root = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse")).expanduser()
h.CACHE_ROOT = cache_root
h.CACHE_POP = cache_root / "population" / "ghsl_wup_2025_1km.tif"

if __name__ == "__main__":
    h.main()
