import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from flood_footprint_qc import select_flood_geometry  # noqa: E402


def square(minx, miny, maxx, maxy, **props):
    return {
        'type': 'Feature',
        'properties': props,
        'geometry': {
            'type': 'Polygon',
            'coordinates': [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
        },
    }


class FloodFootprintQcTests(unittest.TestCase):
    def test_prefers_center_aligned_local_feature_over_large_remote_feature(self):
        features = [
            square(29.0, 36.0, 39.0, 41.5, kind='context'),
            square(40.28, 40.61, 40.31, 40.63, kind='flood affected area'),
        ]
        geom, qc = select_flood_geometry(features, 40.2948, 40.6191)
        self.assertIsNotNone(geom)
        self.assertEqual(qc['status'], 'pass')
        self.assertEqual(qc['selected_feature_index'], 1)
        self.assertTrue(qc['reported_center_inside'])
        self.assertEqual(qc['discarded_polygon_features'], 1)
        self.assertLess(qc['selected_area_km2'], 100.0)
        self.assertEqual(qc['semantic_role'], 'reported_event_area_context')
        self.assertFalse(qc['exposure_grade_eligible'])
        self.assertEqual(qc['inundation_status'], 'not_established')

    def test_preserves_complete_multipolygon_when_feature_is_selected(self):
        feature = {
            'type': 'Feature',
            'properties': {'kind': 'flood event polygon'},
            'geometry': {
                'type': 'MultiPolygon',
                'coordinates': [
                    [[[-72.73, 19.40], [-72.64, 19.40], [-72.64, 19.49], [-72.73, 19.49], [-72.73, 19.40]]],
                    [[[-72.60, 19.42], [-72.58, 19.42], [-72.58, 19.44], [-72.60, 19.44], [-72.60, 19.42]]],
                ],
            },
        }
        geom, qc = select_flood_geometry([feature], -72.6884, 19.4461)
        self.assertIsNotNone(geom)
        self.assertEqual(geom.geom_type, 'MultiPolygon')
        self.assertEqual(qc['selected_feature_index'], 0)
        self.assertFalse(qc['exposure_grade_eligible'])

    def test_fails_closed_when_all_polygon_features_are_far_away(self):
        geom, qc = select_flood_geometry([square(10, 10, 11, 11)], 40.0, 40.0)
        self.assertIsNone(geom)
        self.assertEqual(qc['status'], 'failed')
        self.assertEqual(qc['reason'], 'no_center_aligned_polygon')
        self.assertFalse(qc['exposure_grade_eligible'])
        self.assertEqual(qc['inundation_status'], 'not_established')

    def test_nearby_polygon_can_be_selected_when_point_is_just_outside(self):
        geom, qc = select_flood_geometry([square(40.00, 40.00, 40.20, 40.20)], 40.21, 40.10)
        self.assertIsNotNone(geom)
        self.assertEqual(qc['status'], 'pass')
        self.assertFalse(qc['reported_center_inside'])
        self.assertLess(qc['reported_center_distance_km'], 10.0)
        self.assertFalse(qc['exposure_grade_eligible'])

    def test_very_large_selected_area_is_warned_but_remains_context(self):
        geom, qc = select_flood_geometry([square(100.0, 20.0, 108.0, 26.0, kind='flood affected area')], 104.0, 23.0)
        self.assertIsNotNone(geom)
        self.assertEqual(qc['status'], 'pass')
        self.assertEqual(qc['warning'], 'very_large_reported_event_area_context')
        self.assertFalse(qc['exposure_grade_eligible'])
        self.assertIn('must not be read as flooded land area', qc['warning_interpretation'])


if __name__ == '__main__':
    unittest.main()
