from __future__ import annotations

import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path

import scripts.refresh_gate as gate


class HistoricalRefreshGateDiagnosticTests(unittest.TestCase):
    def test_20260917_semantic_projection_reduces_or_matches_broad_churn(self):
        previous_path = Path("data/events/archive/2026/09/17/003329Z.json")
        current_path = Path("data/events/archive/2026/09/17/082921Z.json")
        if not previous_path.exists() or not current_path.exists():
            self.skipTest("pinned historical production snapshots are not present")
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        current = json.loads(current_path.read_text(encoding="utf-8"))
        semantic, _ = gate.changed_source_ids(previous, current)
        broad, _ = gate.changed_content_source_ids(previous, current)
        scope = gate.incremental_scope(previous, current, force=False)

        old = gate.source_event_map(previous)
        new = gate.source_event_map(current)
        field_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        field_examples: dict[str, list[str]] = defaultdict(list)
        for event_id in sorted(semantic):
            before = old.get(event_id) or {}
            after = new.get(event_id) or {}
            event_type = str(after.get("type") or before.get("type") or "missing")
            type_counts[event_type] += 1
            for field in sorted(set(before) | set(after)):
                if before.get(field) == after.get(field):
                    continue
                field_counts[field] += 1
                if len(field_examples[field]) < 6:
                    field_examples[field].append(event_id)

        print(
            "SEMANTIC_FINGERPRINT_V2_HISTORICAL_DIAGNOSTIC "
            f"broad={len(broad)} semantic={len(semantic)} "
            f"canonical={len(scope['changed_event_ids'])} threshold={scope['threshold']} "
            f"incremental={scope['incremental_refresh']}"
        )
        print("SEMANTIC_CHANGE_TYPES=" + json.dumps(dict(type_counts), sort_keys=True))
        print("SEMANTIC_CHANGE_FIELDS=" + json.dumps(dict(field_counts), sort_keys=True))
        print("SEMANTIC_CHANGE_EXAMPLES=" + json.dumps(dict(field_examples), sort_keys=True))
        self.assertLessEqual(len(semantic), len(broad))
        # This historical busy period contained genuine source changes, but
        # after semantic filtering the scope is still bounded enough for the
        # production incremental executor. This guards against reintroducing
        # the former metadata-driven full-refresh behavior.
        self.assertTrue(scope["incremental_refresh"])
        self.assertLessEqual(len(scope["changed_event_ids"]), scope["threshold"])
        self.assertLessEqual(scope["threshold"], gate.MAX_INCREMENTAL_EVENTS)


if __name__ == "__main__":
    unittest.main()
