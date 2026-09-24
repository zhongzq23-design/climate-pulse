import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_climate_context_enrichment as runner


class ClimateContextCoverageTests(unittest.TestCase):
    def test_union_includes_canonical_only_and_events_only(self):
        snap = {
            "canonical_events": [
                {"id": "canonical-only", "lat": 1.0, "lon": 2.0},
                {"id": "shared", "lat": 3.0, "lon": 4.0, "title": "canonical"},
            ],
            "events": [
                {"id": "events-only", "lat": 5.0, "lon": 6.0},
                {"id": "shared", "lat": 30.0, "lon": 40.0, "extra": "operational"},
            ],
        }
        universe = runner.merged_public_universe(snap)
        self.assertEqual([x["id"] for x in universe], ["canonical-only", "shared", "events-only"])
        shared = next(x for x in universe if x["id"] == "shared")
        self.assertEqual(shared["lat"], 3.0)
        self.assertEqual(shared["extra"], "operational")

    def test_invalid_coordinate_becomes_explicit_unavailable(self):
        universe = runner.merged_public_universe({
            "canonical_events": [{"id": "bad", "lat": None, "lon": 20.0}],
            "events": [],
        })
        ref = universe[0]["climate_context"]
        self.assertEqual(ref["status"], "unavailable")
        self.assertTrue(ref["reason"])

    def test_apply_refs_covers_both_snapshot_views(self):
        original = {
            "events": [{"id": "a", "lat": 1.0, "lon": 2.0}],
            "canonical_events": [
                {"id": "a", "lat": 1.0, "lon": 2.0},
                {"id": "b", "lat": 3.0, "lon": 4.0},
            ],
            "monitor": {},
        }
        enriched = {
            "schema_version": "1.5",
            "monitor": {"climate_context": {"recent_days": 7}},
            "events": [
                {"id": "a", "climate_context": {"status": "unavailable", "reason": "test a"}},
                {"id": "b", "climate_context": {"status": "unavailable", "reason": "test b"}},
            ],
        }
        final, matched, canonical_count = runner.apply_refs_and_assert(original, enriched)
        self.assertEqual(matched, 3)
        self.assertEqual(canonical_count, 2)
        self.assertEqual(final["canonical_events"][1]["climate_context"]["status"], "unavailable")
        self.assertEqual(final["monitor"]["climate_context"]["recent_days"], 7)

    def test_apply_refs_fails_on_unprocessed_public_event(self):
        original = {
            "events": [],
            "canonical_events": [{"id": "missing", "lat": 1.0, "lon": 2.0}],
        }
        enriched = {"events": []}
        with self.assertRaises(RuntimeError):
            runner.apply_refs_and_assert(original, enriched)


if __name__ == "__main__":
    unittest.main()
