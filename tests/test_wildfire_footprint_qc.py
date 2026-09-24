import sys
import unittest
from pathlib import Path

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from wildfire_footprint_qc import (  # noqa: E402
    assess_wildfire_footprint,
    geodesic_area_ha,
    strict_wildfire_qc_pass,
)


class WildfireFootprintQcTests(unittest.TestCase):
    def setUp(self):
        self.geom = Polygon([(30.0, -11.0), (30.1, -11.0), (30.1, -10.9), (30.0, -10.9)])
        self.mapped_area = geodesic_area_ha(self.geom)
        self.provenance = {
            'source': 'GDACS/GWIS',
            'method': 'source_reported_current_episode',
            'gdacs_episode_id': 16,
        }

    def test_passes_episode_aligned_gwis_area_consistent_perimeter(self):
        qc = assess_wildfire_footprint(
            self.geom,
            source_burned_area_ha=self.mapped_area,
            footprint_episode_id=16,
            burned_area_provenance=self.provenance,
        )
        self.assertEqual(qc['status'], 'pass')
        self.assertTrue(qc['episode_aligned'])
        self.assertTrue(qc['source_authority_verified'])
        self.assertTrue(qc['exposure_grade_eligible'])
        self.assertTrue(strict_wildfire_qc_pass(qc))

    def test_fails_when_episode_is_not_aligned(self):
        qc = assess_wildfire_footprint(
            self.geom,
            source_burned_area_ha=self.mapped_area,
            footprint_episode_id=17,
            burned_area_provenance=self.provenance,
        )
        self.assertEqual(qc['status'], 'failed')
        self.assertFalse(qc['episode_aligned'])
        self.assertFalse(strict_wildfire_qc_pass(qc))

    def test_fails_when_source_authority_is_not_verified(self):
        qc = assess_wildfire_footprint(
            self.geom,
            source_burned_area_ha=self.mapped_area,
            footprint_episode_id=16,
            burned_area_provenance={
                'source': 'unknown',
                'method': 'derived',
                'gdacs_episode_id': 16,
            },
        )
        self.assertEqual(qc['status'], 'failed')
        self.assertFalse(qc['source_authority_verified'])

    def test_fails_gross_area_mismatch(self):
        qc = assess_wildfire_footprint(
            self.geom,
            source_burned_area_ha=self.mapped_area / 4.0,
            footprint_episode_id=16,
            burned_area_provenance=self.provenance,
        )
        self.assertEqual(qc['status'], 'failed')
        self.assertIn('grossly_inconsistent', qc['reason'])

    def test_moderate_area_difference_warns_but_passes(self):
        qc = assess_wildfire_footprint(
            self.geom,
            source_burned_area_ha=self.mapped_area / 1.4,
            footprint_episode_id=16,
            burned_area_provenance=self.provenance,
        )
        self.assertEqual(qc['status'], 'pass')
        self.assertEqual(qc['warning'], 'mapped_vs_source_burned_area_difference_gt_25pct')
        self.assertTrue(strict_wildfire_qc_pass(qc))


if __name__ == '__main__':
    unittest.main()
