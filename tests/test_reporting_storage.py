import sys
import unittest
from datetime import date
from pathlib import Path

from shapely.geometry import GeometryCollection, LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from build_periodic_reports import (  # noqa: E402
    display_people, lifecycle_flags, period_specs, publication_status_for,
    record_public_significant,
)
from report_geometry_utils import polygonal_only  # noqa: E402
from update_daily_ledger import geometry_semantics, stable_identity  # noqa: E402


class ReportingStorageTests(unittest.TestCase):
    def test_stable_identity_prefers_gdacs_member(self):
        event = {
            'id': 'eonet-EONET_x', 'origin': 'eonet', 'source_id': 'EONET_x',
            'type': 'Flood',
            'source_members': [
                {'origin': 'eonet', 'source_id': 'EONET_x'},
                {'origin': 'gdacs', 'source_id': '1104131'},
            ],
        }
        self.assertEqual(stable_identity(event), 'gdacs:FL:1104131')

    def test_population_display_rounds_only_at_presentation_layer(self):
        self.assertEqual(display_people(0), '0 people')
        self.assertEqual(display_people(999), '<1,000 people')
        self.assertEqual(display_people(1000), '≈1,000 people')
        self.assertEqual(display_people(2443), '≈2,000 people')
        self.assertEqual(display_people(6868), '≈7,000 people')

    def test_previous_week_is_complete_monday_sunday(self):
        spec = period_specs('previous-week', date(2026, 9, 6))[0]
        self.assertEqual(spec['start'], date(2026, 8, 24))
        self.assertEqual(spec['end'], date(2026, 8, 30))

    def test_previous_month_is_complete_calendar_month(self):
        spec = period_specs('previous-month', date(2026, 9, 6))[0]
        self.assertEqual(spec['start'], date(2026, 8, 1))
        self.assertEqual(spec['end'], date(2026, 8, 31))
        self.assertEqual(spec['id'], '2026-08')

    def test_rolling_periods_end_today(self):
        specs = {x['id']: x for x in period_specs('rolling', date(2026, 9, 6))}
        self.assertEqual(specs['last-7-days']['start'], date(2026, 8, 31))
        self.assertEqual(specs['last-7-days']['end'], date(2026, 9, 6))
        self.assertEqual(specs['month-to-date']['start'], date(2026, 9, 1))
        self.assertEqual(specs['month-to-date']['end'], date(2026, 9, 6))

    def test_frozen_publication_is_withheld_when_day_coverage_is_incomplete(self):
        self.assertEqual(publication_status_for(True, False), 'withheld_incomplete_coverage')
        self.assertEqual(publication_status_for(True, True), 'complete_frozen_publication')
        self.assertEqual(publication_status_for(False, False), 'partial_preview')

    def test_geometry_semantics_separate_exposure_context_and_risk(self):
        self.assertEqual(geometry_semantics('Wildfire', 'GDACS fire polygon')['grade'], 'exposure_grade')
        self.assertEqual(geometry_semantics('Storm', 'GDACS wind polygon')['grade'], 'exposure_grade')
        self.assertEqual(geometry_semantics('Flood', 'GDACS flood event polygon')['grade'], 'context_grade')
        self.assertEqual(geometry_semantics('Drought', 'GDACS drought polygon')['grade'], 'risk_grade')

    def test_legacy_visibility_fails_closed_for_screened_hazards(self):
        self.assertFalse(record_public_significant({'type': 'Wildfire'}))
        self.assertFalse(record_public_significant({'type': 'Storm'}))
        self.assertTrue(record_public_significant({'type': 'Flood'}))
        self.assertTrue(record_public_significant({'type': 'Drought'}))

    def test_lifecycle_new_ongoing_and_resolved(self):
        start, end = date(2026, 9, 1), date(2026, 9, 7)
        new = {'days_observed': ['2026-09-03', '2026-09-07'], 'lifecycle_first_seen': '2026-09-03T00:00:00Z', 'lifecycle_last_seen': '2026-09-07T00:00:00Z'}
        old = {'days_observed': ['2026-09-01', '2026-09-07'], 'lifecycle_first_seen': '2026-08-20T00:00:00Z', 'lifecycle_last_seen': '2026-09-07T00:00:00Z'}
        resolved = {'days_observed': ['2026-09-01', '2026-09-04'], 'lifecycle_first_seen': '2026-08-20T00:00:00Z', 'lifecycle_last_seen': '2026-09-04T00:00:00Z'}
        self.assertEqual(lifecycle_flags(new, start, end, True)['label'], 'New this period')
        self.assertEqual(lifecycle_flags(old, start, end, True)['label'], 'Ongoing')
        self.assertEqual(lifecycle_flags(resolved, start, end, True)['label'], 'Resolved this period')
        self.assertFalse(lifecycle_flags(resolved, start, end, False)['resolved_this_period'])

    def test_polygonal_only_keeps_polygon_parts_of_geometry_collection(self):
        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        mixed = GeometryCollection([poly, LineString([(0, 0), (2, 2)])])
        out = polygonal_only(mixed)
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out.area, 1.0)

    def test_polygonal_only_rejects_non_polygon_collection(self):
        mixed = GeometryCollection([LineString([(0, 0), (2, 2)])])
        self.assertIsNone(polygonal_only(mixed))


if __name__ == '__main__':
    unittest.main()
