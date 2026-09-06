import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from build_periodic_reports import display_people, period_specs  # noqa: E402
from update_daily_ledger import stable_identity  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
