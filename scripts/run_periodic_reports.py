#!/usr/bin/env python3
"""Compatibility runner for periodic reports with polygon-only raster guards."""
from __future__ import annotations

import build_periodic_reports as reports
from report_geometry_utils import polygonal_only

_original_population = reports.h.raster_population
_original_area = reports.raster_area_ha


def safe_population(src, geom4326, buffer_m: float = 0.0):
    geom = polygonal_only(geom4326)
    if geom is None:
        return 0
    return _original_population(src, geom, buffer_m=buffer_m)


def safe_area(path, geom4326, band: int = 1):
    geom = polygonal_only(geom4326)
    if geom is None:
        return None
    return _original_area(path, geom, band)


reports.h.raster_population = safe_population
reports.raster_area_ha = safe_area

if __name__ == "__main__":
    reports.main()
