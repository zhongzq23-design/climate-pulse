#!/usr/bin/env python3
"""Production entry point for the Climate Pulse refresh gate.

The semantic comparison implementation lives in ``refresh_gate_v2.py``.  This
entry point adds the production incremental-scope policy after the semantic
projection has removed feed-republication churn.

The original 25% cap was deliberately conservative while the gate compared
broad source JSON.  Once changes are semantic and every changed current source
maps unambiguously to a canonical event, incremental execution is correct for a
larger bounded subset.  Production therefore allows at most 50% of the current
canonical universe, capped at 60 events.  Anything larger, any unresolved
mapping, or any manual dispatch still falls back to the full pipeline.
"""
from __future__ import annotations

import math
from typing import Any

try:  # package import used by tests
    from .refresh_gate_v2 import *  # noqa: F401,F403
    from . import refresh_gate_v2 as _impl
except ImportError:  # direct `python scripts/refresh_gate.py`
    from refresh_gate_v2 import *  # type: ignore # noqa: F401,F403
    import refresh_gate_v2 as _impl  # type: ignore


MIN_INCREMENTAL_EVENTS = 12
MAX_INCREMENTAL_FRACTION = 0.50
MAX_INCREMENTAL_EVENTS = 60
_base_incremental_scope = _impl.incremental_scope


def incremental_scope(
    previous: dict[str, Any], current: dict[str, Any], force: bool
) -> dict[str, Any]:
    """Apply the bounded production cap to the semantic V2 source diff."""
    result = _base_incremental_scope(previous, current, force)
    if force or not result.get("changed_source_ids"):
        return result

    current_canonical_ids = {
        str(event.get("id"))
        for event in current.get("canonical_events") or []
        if isinstance(event, dict) and event.get("id") is not None
    }
    n = len(current_canonical_ids)
    if not n:
        result["incremental_refresh"] = False
        result["threshold"] = 0
        return result

    threshold = min(
        MAX_INCREMENTAL_EVENTS,
        max(MIN_INCREMENTAL_EVENTS, int(math.ceil(n * MAX_INCREMENTAL_FRACTION))),
    )
    changed_count = len(result.get("changed_event_ids") or [])
    unresolved = result.get("unresolved_current_source_ids") or []
    result["threshold"] = threshold
    result["incremental_refresh"] = bool(not unresolved and changed_count <= threshold)
    return result


def main() -> int:
    """Run V2 with the production cap without leaking monkey-patches to imports."""
    original = _impl.incremental_scope
    try:
        _impl.incremental_scope = incremental_scope
        return _impl.main()
    finally:
        _impl.incremental_scope = original


if __name__ == "__main__":
    raise SystemExit(main())
