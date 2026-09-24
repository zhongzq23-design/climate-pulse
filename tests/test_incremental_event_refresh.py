from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_gate as gate  # noqa: E402
import run_incremental_event_refresh as runner  # noqa: E402


class IncrementalRefreshGateTests(unittest.TestCase):
    def test_relative_updated_label_does_not_trigger_source_change(self):
        previous = {
            "source_events": [
                {
                    "id": "gdacs-WF-1",
                    "origin": "gdacs",
                    "source_id": "1",
                    "type": "Wildfire",
                    "updated": "Updated 1 h ago",
                    "event_date": "2026-09-16T10:00:00Z",
                }
            ]
        }
        current = {
            "source_events": [
                {
                    "id": "gdacs-WF-1",
                    "origin": "gdacs",
                    "source_id": "1",
                    "type": "Wildfire",
                    "updated": "Updated 9 h ago",
                    "event_date": "2026-09-16T10:00:00Z",
                }
            ]
        }
        self.assertEqual(gate.event_fingerprint(previous), gate.event_fingerprint(current))
        changed, removed = gate.changed_source_ids(previous, current)
        self.assertEqual(changed, set())
        self.assertEqual(removed, set())

    def test_real_source_change_maps_to_merged_canonical_id(self):
        # A feed-republication timestamp alone is deliberately not a heavy
        # change under SOURCE_CHANGE_SEMANTIC_FINGERPRINT_V2.  Use an actual
        # event-window change here so the test continues to verify source ->
        # merged-canonical mapping rather than metadata churn.
        previous = {
            "source_events": [
                {
                    "id": "raw-a",
                    "origin": "gdacs",
                    "source_id": "a",
                    "type": "Storm",
                    "event_end": "2026-09-16T10:00:00Z",
                    "event_date": "2026-09-16T10:00:00Z",
                }
            ],
            "canonical_events": [
                {"id": "canonical-1", "type": "Storm", "source_members": [{"id": "raw-a", "origin": "gdacs", "source_id": "a", "type": "Storm"}]}
            ],
        }
        current = {
            "source_events": [
                {
                    "id": "raw-a",
                    "origin": "gdacs",
                    "source_id": "a",
                    "type": "Storm",
                    "event_end": "2026-09-16T12:00:00Z",
                    "event_date": "2026-09-16T12:00:00Z",
                }
            ],
            "canonical_events": [
                {"id": "canonical-1", "type": "Storm", "source_members": [{"id": "raw-a", "origin": "gdacs", "source_id": "a", "type": "Storm"}]}
            ],
        }
        scope = gate.incremental_scope(previous, current, force=False)
        self.assertTrue(scope["incremental_refresh"])
        self.assertEqual(scope["changed_event_ids"], ["canonical-1"])
        self.assertEqual(scope["unresolved_current_source_ids"], [])

    def test_removed_source_member_maps_to_surviving_canonical(self):
        previous = {
            "source_events": [
                {"id": "raw-a", "origin": "gdacs", "source_id": "a", "type": "Storm"},
                {"id": "raw-b", "origin": "eonet", "source_id": "b", "type": "Storm"},
            ],
            "canonical_events": [
                {"id": "canonical-1", "type": "Storm", "source_members": [{"id": "raw-a"}, {"id": "raw-b"}]}
            ],
        }
        current = {
            "source_events": [
                {"id": "raw-a", "origin": "gdacs", "source_id": "a", "type": "Storm"}
            ],
            "canonical_events": [
                {"id": "canonical-1", "type": "Storm", "source_members": [{"id": "raw-a"}]}
            ],
        }
        scope = gate.incremental_scope(previous, current, force=False)
        self.assertTrue(scope["incremental_refresh"])
        self.assertEqual(scope["changed_event_ids"], ["canonical-1"])
        self.assertEqual(scope["removed_source_ids"], ["raw-b"])


class IncrementalSeedTests(unittest.TestCase):
    def test_seed_reuses_expensive_products_only_for_unchanged_events(self):
        previous = {
            "monitor": {"old_policy": "keep"},
            "hazard_exposure": {"diagnostics": {"wildfire_enriched": 1}},
            "canonical_events": [
                {
                    "id": "same",
                    "type": "Wildfire",
                    "burned_area_ha": 12345,
                    "event_date": "2026-09-15T00:00:00Z",
                    "summary": "aligned summary",
                    "exposure": {"population_within_5km": 20000},
                    "footprint": {"status": "ready", "path": "data/footprints/same.json"},
                    "source_metrics": {"wildfire_current_episode_id": 7},
                    "display_eligible": True,
                },
                {
                    "id": "changed",
                    "type": "Wildfire",
                    "burned_area_ha": 20000,
                    "exposure": {"population_within_5km": 30000},
                },
            ],
        }
        current = {
            "monitor": {"new_policy": "fresh"},
            "canonical_events": [
                {"id": "same", "type": "Wildfire", "burned_area_ha": 99999},
                {"id": "changed", "type": "Wildfire", "burned_area_ha": 25000},
            ],
        }
        seeded = runner.seed_unchanged_products(previous, current, {"changed"})
        rows = runner.by_id(seeded["canonical_events"])
        self.assertEqual(rows["same"]["burned_area_ha"], 12345)
        self.assertEqual(rows["same"]["event_date"], "2026-09-15T00:00:00Z")
        self.assertEqual(rows["same"]["summary"], "aligned summary")
        self.assertEqual(rows["same"]["exposure"]["population_within_5km"], 20000)
        self.assertEqual(rows["same"]["source_metrics"]["wildfire_current_episode_id"], 7)
        self.assertTrue(rows["same"]["display_eligible"])
        self.assertEqual(rows["changed"]["burned_area_ha"], 25000)
        self.assertNotIn("exposure", rows["changed"])
        self.assertEqual(seeded["monitor"]["old_policy"], "keep")
        self.assertEqual(seeded["monitor"]["new_policy"], "fresh")


if __name__ == "__main__":
    unittest.main()
