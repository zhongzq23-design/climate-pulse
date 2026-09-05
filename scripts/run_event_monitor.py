#!/usr/bin/env python3
"""Policy wrapper for the Climate Pulse event monitor.

Keep the 10,000 ha gate for Green wildfire records while retaining GDACS
Orange/Red fires even when burned area is smaller. Green population eligibility
is applied later by run_hazard_enrichment.py.
"""
from __future__ import annotations

import monitor_events as m

_original_parse_gdacs = m.parse_gdacs
_original_threshold = m.WILDFIRE_MIN_HA


def parse_gdacs_severity_aware(data, now, diag):
    m.WILDFIRE_MIN_HA = 0.0
    try:
        rows = _original_parse_gdacs(data, now, diag)
    finally:
        m.WILDFIRE_MIN_HA = _original_threshold
    kept = []
    dropped_green = 0
    for e in rows:
        if e.get("type") != "Wildfire":
            kept.append(e); continue
        source = str(e.get("source") or "").lower()
        high_alert = "orange" in source or "red" in source
        try:
            large_enough = float(e.get("burned_area_ha")) >= _original_threshold
        except (TypeError, ValueError):
            large_enough = False
        if high_alert or large_enough:
            kept.append(e)
        else:
            dropped_green += 1
    if dropped_green:
        diag["wildfire_below_threshold"] = int(diag.get("wildfire_below_threshold", 0)) + dropped_green
        diag["wildfire_major"] = max(0, int(diag.get("wildfire_major", 0)) - dropped_green)
    return kept


m.parse_gdacs = parse_gdacs_severity_aware

if __name__ == "__main__":
    m.main()
