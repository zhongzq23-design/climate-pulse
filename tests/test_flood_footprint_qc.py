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
        # Reproduces the failure mode seen for GDACS FL 1104131: a large remote
        # polygon should not dominate a small source feature around the reported
        # Trabzon/Uzungol event coordinate.
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

    def test_fails_closed_when_all_polygon_features_are_far_away(self):
        geom, qc = select_flood_geometry([square(10, 10, 11, 11)], 40.0, 40.0)
        self.assertIsNone(geom)
        self.assertEqual(qc['status'], 'failed')
        self.assertEqual(qc['reason'], 'no_center_aligned_polygon')

    def test_nearby_polygon_can_be_selected_when_point_is_just_outside(self):
        geom, qc = select_flood_geometry([square(40.00, 40.00, 40.20, 40.20)], 40.21, 40.10)
        self.assertIsNotNone(geom)
        self.assertEqual(qc['status'], 'pass')
        self.assertFalse(qc['reported_center_inside'])
        self.assertLess(qc['reported_center_distance_km'], 10.0)


if __name__ == '__main__':
    unittest.main()
