import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from wildfire_identity_policy import dedupe_events_preserve_wildfires, individual_display  # noqa: E402


def event(source_id, typ='Wildfire', lat=10.0, lon=20.0, origin='gdacs', gdacs_id=None):
    row = {
        'id': f'{origin}-{source_id}',
        'source_id': str(source_id),
        'origin': origin,
        'type': typ,
        'title': f'{typ} {source_id}',
        'region': 'Test',
        'lat': lat,
        'lon': lon,
        'status': 'Recent',
        'updated': 'Updated 1 h ago',
        'priority': 'Standard',
        'source': 'GDACS' if origin == 'gdacs' else 'Copernicus CEMS',
        'source_url': f'https://example.invalid/{source_id}',
        'event_date': '2026-09-09T10:00:00Z',
        'burned_area_ha': 20000 if typ == 'Wildfire' else None,
    }
    if gdacs_id is not None:
        row['gdacs_id'] = str(gdacs_id)
    return row


class WildfireIdentityPolicyTests(unittest.TestCase):
    def test_nearby_gdacs_wildfires_remain_separate(self):
        rows = [
            event('1030001', lat=10.00, lon=20.00),
            event('1030002', lat=10.05, lon=20.05),
            event('1030003', lat=10.10, lon=20.10),
        ]
        got = dedupe_events_preserve_wildfires(rows)
        self.assertEqual(len(got), 3)
        self.assertEqual({x['source_id'] for x in got}, {'1030001', '1030002', '1030003'})
        self.assertFalse(any(x.get('origin') == 'cluster' for x in got))

    def test_wildfire_proximity_alone_never_merges_cross_source_records(self):
        rows = [
            event('1030001', lat=10.00, lon=20.00, origin='gdacs'),
            event('EMSR999', lat=10.01, lon=20.01, origin='cems'),
        ]
        got = dedupe_events_preserve_wildfires(rows)
        self.assertEqual(len(got), 2)

    def test_explicit_cems_gdacs_link_can_dedupe_same_fire(self):
        rows = [
            event('1030001', lat=10.00, lon=20.00, origin='gdacs'),
            event('EMSR999', lat=10.01, lon=20.01, origin='cems', gdacs_id='1030001'),
        ]
        got = dedupe_events_preserve_wildfires(rows)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['source_id'], '1030001')
        self.assertEqual(len(got[0]['source_members']), 2)

    def test_non_wildfire_proximity_dedupe_is_unchanged(self):
        rows = [
            event('FL1', typ='Flood', lat=10.00, lon=20.00, origin='gdacs'),
            event('EMSR1', typ='Flood', lat=10.01, lon=20.01, origin='cems'),
        ]
        got = dedupe_events_preserve_wildfires(rows)
        self.assertEqual(len(got), 1)

    def test_individual_display_never_creates_cluster(self):
        rows = [event('1030001'), event('1030002', lat=10.02, lon=20.02)]
        got = individual_display(rows)
        self.assertEqual(len(got), 2)
        self.assertFalse(any(x.get('member_count') for x in got))
        self.assertFalse(any(x.get('origin') == 'cluster' for x in got))


if __name__ == '__main__':
    unittest.main()
