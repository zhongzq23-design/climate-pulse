import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from monitor_events import date_label, parse_gdacs  # noqa: E402


class TemporalModelTests(unittest.TestCase):
    def test_future_timestamp_is_not_reported_as_updated_now(self):
        now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        self.assertEqual(date_label((now + timedelta(days=1)).isoformat(), now), 'Update time unavailable')

    def test_gdacs_keeps_update_start_and_end_separate(self):
        now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        data = {'features': [{
            'geometry': {'type': 'Point', 'coordinates': [40.29, 40.62]},
            'properties': {
                'eventtype': 'FL', 'eventid': 1104131, 'country': 'Türkiye',
                'fromdate': '2026-09-01T00:00:00',
                'todate': '2026-09-10T00:00:00',
                'lastupdate': '2026-09-06T10:00:00',
                'alertlevel': 'Green', 'severitydata': {'severitytext': 'Magnitude 0'},
            },
        }]}
        diag = {'wildfire_raw': 0, 'wildfire_with_area': 0, 'wildfire_major': 0, 'wildfire_below_threshold': 0, 'wildfire_area_unknown': 0}
        event = parse_gdacs(data, now, diag)[0]
        self.assertEqual(event['source_updated_at'], '2026-09-06T10:00:00')
        self.assertEqual(event['event_start'], '2026-09-01T00:00:00')
        self.assertEqual(event['event_end'], '2026-09-10T00:00:00')
        self.assertEqual(event['event_date'], '2026-09-06T10:00:00')
        self.assertEqual(event['updated'], 'Updated 2 h ago')


if __name__ == '__main__':
    unittest.main()
