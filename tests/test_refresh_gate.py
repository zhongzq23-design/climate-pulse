from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.refresh_gate_v2 as gate
from scripts.refresh_gate_v2 import event_fingerprint


class RefreshGateFingerprintTests(unittest.TestCase):
    def source_event(self, event_id="gdacs-WF-1", area=15000):
        return {
            "id": event_id,
            "origin": "gdacs",
            "source_id": event_id.rsplit("-", 1)[-1],
            "type": "Wildfire",
            "title": "Wildfire in Example",
            "region": "Example",
            "lat": 10.0,
            "lon": 20.0,
            "status": "Recent",
            "priority": "Standard",
            "summary": f"Burned area: {area:,} ha. Wildfire event tracked by GDACS.",
            "source": "GDACS · Green",
            "source_url": "https://example.test/report",
            "updated": "Updated 1 h ago",
            "event_date": "2026-09-15T18:00:00Z",
            "source_updated_at": "2026-09-15T18:00:00Z",
            "event_start": "2026-09-10T00:00:00Z",
            "event_end": "2026-09-15T18:00:00Z",
            "last_detection": "2026-09-15T18:00:00Z",
            "burned_area_ha": area,
        }

    def snapshot(self):
        event = self.source_event()
        return {
            "generated_at": "2026-09-16T00:17:00Z",
            "source_events": [event],
            "canonical_events": [copy.deepcopy(event)],
        }

    def test_generated_and_enrichment_fields_do_not_change_semantic_fingerprint(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        b["generated_at"] = "2026-09-16T08:17:00Z"
        b["source_events"][0]["climate_context"] = {"status": "ready"}
        b["source_events"][0]["exposure"] = {"population_direct": 123}
        self.assertEqual(event_fingerprint(a), event_fingerprint(b))

    def test_feed_republication_fields_do_not_force_expensive_refresh(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        row = b["source_events"][0]
        row["updated"] = "Updated <1 hour ago"
        row["source_updated_at"] = "2026-09-16T08:05:00Z"
        row["event_date"] = "2026-09-16T08:05:00Z"
        row["source_url"] = "https://example.test/report?cache=2"
        self.assertEqual(event_fingerprint(a), event_fingerprint(b))
        self.assertNotEqual(gate.content_fingerprint(a), gate.content_fingerprint(b))

    def test_source_metric_change_changes_fingerprint(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        b["source_events"][0]["burned_area_ha"] = 16000
        b["source_events"][0]["summary"] = "Burned area: 16,000 ha. Wildfire event tracked by GDACS."
        self.assertNotEqual(event_fingerprint(a), event_fingerprint(b))

    def test_severity_summary_change_changes_fingerprint(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        b["source_events"][0]["summary"] = "Wildfire severity increased. Alert level: Orange."
        self.assertNotEqual(event_fingerprint(a), event_fingerprint(b))

    def test_event_time_formatting_is_normalized(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        b["source_events"][0]["event_start"] = "2026-09-10T02:00:00+02:00"
        self.assertEqual(event_fingerprint(a), event_fingerprint(b))

    def test_source_event_order_does_not_change_fingerprint(self):
        a = self.snapshot()
        a["source_events"].append(
            {
                "id": "gdacs-FL-2",
                "origin": "gdacs",
                "source_id": "2",
                "type": "Flood",
                "lat": 0.0,
                "lon": 1.0,
                "status": "Recent",
                "priority": "Standard",
                "event_start": "2026-09-14T00:00:00Z",
                "event_end": "2026-09-15T00:00:00Z",
                "summary": "Magnitude 0 Alert level: Green.",
            }
        )
        b = copy.deepcopy(a)
        b["source_events"].reverse()
        self.assertEqual(event_fingerprint(a), event_fingerprint(b))

    def test_bulk_feed_churn_maps_only_real_semantic_change(self):
        previous = {"source_events": [], "canonical_events": []}
        for i in range(40):
            event = self.source_event(f"gdacs-WF-{i}", 15000 + i)
            previous["source_events"].append(event)
            previous["canonical_events"].append(copy.deepcopy(event))
        current = copy.deepcopy(previous)
        for event in current["source_events"]:
            event["source_updated_at"] = "2026-09-17T08:05:00Z"
            event["event_date"] = "2026-09-17T08:05:00Z"
            event["updated"] = "Updated <1 hour ago"
        current["source_events"][0]["burned_area_ha"] += 1000
        current["source_events"][0]["summary"] = "Burned area: 16,000 ha. Wildfire event tracked by GDACS."

        semantic_changed, _ = gate.changed_source_ids(previous, current)
        broad_changed, _ = gate.changed_content_source_ids(previous, current)
        scope = gate.incremental_scope(previous, current, force=False)

        self.assertEqual(semantic_changed, {"gdacs-WF-0"})
        self.assertEqual(len(broad_changed), 40)
        self.assertTrue(scope["incremental_refresh"])
        self.assertEqual(scope["changed_event_ids"], ["gdacs-WF-0"])
        self.assertEqual(scope["threshold"], 12)


class RefreshGateModeTests(unittest.TestCase):
    def snapshot(self, generated_at="2026-09-16T08:17:00Z", area=15000):
        event = {
            "id": "gdacs-WF-1",
            "origin": "gdacs",
            "source_id": "1",
            "type": "Wildfire",
            "lat": 10.0,
            "lon": 20.0,
            "status": "Recent",
            "priority": "Standard",
            "summary": f"Burned area: {area:,} ha. Wildfire event tracked by GDACS.",
            "burned_area_ha": area,
            "event_start": "2026-09-10T00:00:00Z",
            "event_end": "2026-09-15T18:00:00Z",
            "last_detection": "2026-09-15T18:00:00Z",
            "source_updated_at": "2026-09-15T18:00:00Z",
            "event_date": "2026-09-15T18:00:00Z",
        }
        return {
            "generated_at": generated_at,
            "source_events": [event],
            "canonical_events": [copy.deepcopy(event)],
        }

    def run_gate(self, previous, current, prior_cycle, latest_cycle, event="schedule"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prev = root / "previous.json"
            cur = root / "current.json"
            idx = root / "index.json"
            out = root / "github-output.txt"
            prev.write_text(json.dumps(previous), encoding="utf-8")
            cur.write_text(json.dumps(current), encoding="utf-8")
            idx.write_text(
                json.dumps({"recent_window": {"daily_cycle_times": [prior_cycle]}}),
                encoding="utf-8",
            )
            argv = [
                "refresh_gate.py",
                "--previous",
                str(prev),
                "--current",
                str(cur),
                "--climate-index",
                str(idx),
                "--github-event",
                event,
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                gate, "latest_complete_ifs_cycle", return_value=latest_cycle
            ), mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
                self.assertEqual(gate.main(), 0)
            values = {}
            for line in out.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            return values

    def test_semantic_source_change_runs_incremental_full_refresh(self):
        previous = self.snapshot(area=15000)
        current = self.snapshot(area=16000)
        values = self.run_gate(previous, current, "2026-09-15T06:00:00Z", "2026-09-15T12:00:00Z")
        self.assertEqual(values["full_refresh"], "true")
        self.assertEqual(values["incremental_refresh"], "true")
        self.assertEqual(values["changed_event_count"], "1")
        self.assertEqual(values["climate_refresh"], "true")
        self.assertEqual(values["any_refresh"], "true")

    def test_feed_timestamp_churn_does_not_force_full_refresh(self):
        previous = self.snapshot()
        current = self.snapshot(generated_at="2026-09-16T16:17:00Z")
        current["source_events"][0]["source_updated_at"] = "2026-09-16T15:55:00Z"
        current["source_events"][0]["event_date"] = "2026-09-16T15:55:00Z"
        cycle = "2026-09-15T06:00:00Z"
        values = self.run_gate(previous, current, cycle, cycle)
        self.assertEqual(values["full_refresh"], "false")
        self.assertEqual(values["changed_source_count"], "0")
        self.assertEqual(values["suppressed_source_churn_count"], "1")
        self.assertEqual(values["any_refresh"], "false")

    def test_ifs_change_runs_climate_only(self):
        previous = self.snapshot()
        current = self.snapshot(generated_at="2026-09-16T16:17:00Z")
        values = self.run_gate(previous, current, "2026-09-15T06:00:00Z", "2026-09-15T12:00:00Z")
        self.assertEqual(values["full_refresh"], "false")
        self.assertEqual(values["climate_refresh"], "true")
        self.assertEqual(values["maintenance_refresh"], "false")

    def test_new_day_runs_maintenance_without_forcing_full(self):
        previous = self.snapshot(generated_at="2026-09-16T16:17:00Z")
        current = self.snapshot(generated_at="2026-09-17T00:17:00Z")
        cycle = "2026-09-16T00:00:00Z"
        values = self.run_gate(previous, current, cycle, cycle)
        self.assertEqual(values["full_refresh"], "false")
        self.assertEqual(values["climate_refresh"], "false")
        self.assertEqual(values["maintenance_refresh"], "true")
        self.assertEqual(values["any_refresh"], "true")

    def test_no_change_is_true_noop(self):
        previous = self.snapshot()
        current = self.snapshot(generated_at="2026-09-16T16:17:00Z")
        cycle = "2026-09-15T06:00:00Z"
        values = self.run_gate(previous, current, cycle, cycle)
        self.assertEqual(values["full_refresh"], "false")
        self.assertEqual(values["climate_refresh"], "false")
        self.assertEqual(values["maintenance_refresh"], "false")
        self.assertEqual(values["any_refresh"], "false")

    def test_manual_dispatch_forces_all_layers(self):
        previous = self.snapshot()
        current = self.snapshot()
        cycle = "2026-09-15T06:00:00Z"
        values = self.run_gate(previous, current, cycle, cycle, event="workflow_dispatch")
        self.assertEqual(values["full_refresh"], "true")
        self.assertEqual(values["climate_refresh"], "true")
        self.assertEqual(values["maintenance_refresh"], "true")


if __name__ == "__main__":
    unittest.main()
