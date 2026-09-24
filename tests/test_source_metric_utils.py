import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from source_metric_utils import (  # noqa: E402
    drought_agricultural_impact_area,
    episode_details_url,
    gdacs_alert_level,
    impact_resources,
    parse_cyclone_timeline,
    parse_wildfire_impact,
)


class SourceMetricUtilsTests(unittest.TestCase):
    def test_episode_details_url_uses_requested_episode(self):
        payload = {'properties': {'episodes': [
            {'episodeid': 1, 'details': 'https://www.gdacs.org/x/getepisodedata?episodeid=1'},
            {'episodeid': 3, 'details': 'https://www.gdacs.org/x/getepisodedata?episodeid=3'},
        ]}}
        self.assertIn('episodeid=3', episode_details_url(payload, 3))

    def test_impact_resources(self):
        payload = {'properties': {'impacts': [
            {'source': 'GWIS', 'resource': {'impact': 'https://www.gdacs.org/impact/1'}},
            {'source': 'JTWC', 'resource': {'timeline': 'https://www.gdacs.org/timeline/1'}},
        ]}}
        self.assertEqual(impact_resources(payload, 'impact'), [('GWIS', 'https://www.gdacs.org/impact/1')])

    def test_wildfire_direct_key_shape(self):
        payload = {'modelname': 'WF', 'datums': [{'datum': [{
            'datasource': 'POP', 'POPAFFECTED': '1,234', 'SUMPOP1.0': '2,000',
            'SUMPOP2.0': '3,000', 'SUMPOP5.0': '5,000', 'SUMPOP10.0': '9,000'
        }]}]}
        got = parse_wildfire_impact(payload)
        self.assertEqual(got['population_burned_area'], 1234)
        self.assertEqual(got['population_within_5km'], 5000)
        self.assertEqual(got['population_within_10km'], 9000)

    def test_wildfire_name_value_shape(self):
        payload = {'datums': [{'datum': [{'datasource': 'POP', 'scalars': [
            {'name': 'POPAFFECTED', 'value': '42'}, {'name': 'SUMPOP5.0', 'value': '101'},
        ]}]}]}
        got = parse_wildfire_impact(payload)
        self.assertEqual(got['population_burned_area'], 42)
        self.assertEqual(got['population_within_5km'], 101)

    def test_cyclone_prefers_current_actual_and_ignores_pop(self):
        payload = {'channel': {'item': [
            {'id': 'a', 'actual': 'True', 'current': 'false', 'advisory_datetime': '25 Jul 2026 18:00', 'pop': '999', 'pop39': '100', 'pop74': '10'},
            {'id': 'b', 'actual': 'True', 'current': 'true', 'advisory_datetime': '26 Jul 2026 00:00', 'pop': '888', 'pop39': '200', 'pop74': '20', 'popstormsurge': '5'},
            {'id': 'c', 'actual': 'False', 'current': 'false', 'advisory_datetime': '27 Jul 2026 00:00', 'pop39': '300', 'pop74': '30'},
        ]}}
        got = parse_cyclone_timeline(payload)
        self.assertEqual(got['id'], 'b')
        self.assertEqual(got['population_wind_39kt'], 200)
        self.assertEqual(got['population_wind_74kt'], 20)
        self.assertEqual(got['population_storm_surge'], 5)
        self.assertNotIn('pop', got)

    def test_drought_area_requires_drought_impact_context(self):
        self.assertEqual(drought_agricultural_impact_area('Medium impact for agricultural drought in 1,412,468 km². Alert level: Orange.'), 1412468.0)
        self.assertIsNone(drought_agricultural_impact_area('Mapped footprint 1,412,468 km²'))

    def test_alert_level(self):
        self.assertEqual(gdacs_alert_level({'source': 'GDACS · Red'}), 'Red')
        self.assertEqual(gdacs_alert_level({'alert_level': 'orange'}), 'Orange')


if __name__ == '__main__':
    unittest.main()
