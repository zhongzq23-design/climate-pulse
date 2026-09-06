import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from finalize_wildfire_display import wildfire_eligible  # noqa: E402
from restore_recent_wildfires import recent_by_last_detection  # noqa: E402


class WildfireDisplayFreshnessTests(unittest.TestCase):
    def test_recent_long_running_fire_is_retained_by_last_detection(self):
        now = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)
        event = {'event_date': '2026-09-05'}
        self.assertTrue(recent_by_last_detection(event, now))

    def test_old_last_detection_is_not_recovered(self):
        now = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)
        event = {'event_date': '2026-08-20'}
        self.assertFalse(recent_by_last_detection(event, now))

    def test_green_fire_exactly_10000_people_is_visible(self):
        event = {
            'type': 'Wildfire',
            'priority': 'Standard',
            'burned_area_ha': 16194,
            'exposure': {'population_within_5km': 10000},
        }
        eligible, rule = wildfire_eligible(event)
        self.assertTrue(eligible)
        self.assertIn('gte_10000', rule)

    def test_green_fire_below_population_boundary_is_hidden(self):
        event = {
            'type': 'Wildfire',
            'priority': 'Standard',
            'burned_area_ha': 16194,
            'exposure': {'population_within_5km': 9999},
        }
        eligible, _ = wildfire_eligible(event)
        self.assertFalse(eligible)


if __name__ == '__main__':
    unittest.main()
