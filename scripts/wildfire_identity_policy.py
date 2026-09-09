#!/usr/bin/env python3
"""Wildfire identity/display policy for Climate Pulse.

Distinct wildfire source records are not merged merely because they are close in
space or time. GDACS wildfire event IDs remain separate. The only wildfire
records that may be collapsed are exact/explicit source duplicates, e.g. a CEMS
record carrying a ``gdacsId`` that points to the same GDACS wildfire event.

Non-wildfire hazards keep the existing proximity-based cross-source deduplication.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import monitor_events as m

_BASE_DEDUPE = m.dedupe


def _stamp(event: dict[str, Any]) -> float:
    dt = m.parse_dt(event.get("event_date"))
    return dt.timestamp() if dt else 0.0


def _wildfire_identity(event: dict[str, Any]) -> tuple[str, str]:
    """Return an explicit wildfire identity; never derive it from proximity."""
    origin = str(event.get("origin") or "").lower()
    source_id = str(event.get("source_id") or event.get("id") or "")
    if origin == "gdacs" and source_id:
        return ("gdacs", source_id)
    linked_gdacs = event.get("gdacs_id")
    if linked_gdacs not in (None, ""):
        return ("gdacs", str(linked_gdacs))
    return (origin or "source", source_id)


def _merge_explicit_duplicates(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge only records already proven to share an explicit source identity."""
    # Prefer the GDACS record as the canonical presentation record when present;
    # otherwise use the most recently updated source record.
    primary = max(
        group,
        key=lambda e: (1 if str(e.get("origin") or "").lower() == "gdacs" else 0, _stamp(e)),
    )
    out = deepcopy(primary)
    urls: list[str] = []
    members: list[dict[str, str]] = []
    sources: list[str] = []
    for event in group:
        for url in [*(event.get("source_urls") or []), event.get("source_url")]:
            if url and url not in urls:
                urls.append(str(url))
        member = {"origin": str(event.get("origin") or ""), "source_id": str(event.get("source_id") or "")}
        if member["origin"] and member["source_id"] and member not in members:
            members.append(member)
        src = str(event.get("source") or "")
        if src and src not in sources:
            sources.append(src)
    out["source_urls"] = urls
    out["source_members"] = members
    if sources:
        out["source"] = " · ".join(sources)
    return out


def dedupe_events_preserve_wildfires(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep distinct wildfire IDs separate; preserve legacy dedupe for other hazards."""
    wildfires = [deepcopy(e) for e in events if e.get("type") == "Wildfire"]
    others = [deepcopy(e) for e in events if e.get("type") != "Wildfire"]

    # Keep the established cross-source proximity dedupe for non-wildfire hazards.
    out = _BASE_DEDUPE(others)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in wildfires:
        groups.setdefault(_wildfire_identity(event), []).append(event)
    out.extend(_merge_explicit_duplicates(group) for group in groups.values())
    return individual_display(out)


def individual_display(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one display record per canonical event; never create wildfire clusters."""
    rows = [deepcopy(e) for e in events]
    rows.sort(
        key=lambda e: m.parse_dt(e.get("event_date")) or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    return rows
