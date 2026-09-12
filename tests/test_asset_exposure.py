import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import enrich_asset_exposure as asset  # noqa: E402


class AssetExposureTests(unittest.TestCase):
    def test_flood_population_context_uses_qc_polygon_population(self):
        geom = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        pop_src = object()
        with patch.object(asset.h, 'raster_population', return_value=12345) as raster_population:
            result = asset.flood_population_context(geom, pop_src)

        raster_population.assert_called_once_with(pop_src, geom)
        self.assertEqual(result['potentially_affected_population'], 12345)
        self.assertEqual(result['potentially_affected_population_reference'], 'JRC GHSL GHS-WUP-POP R2025A, epoch 2025')
        self.assertIn('not source-reported affected population', result['potentially_affected_population_interpretation'])
        self.assertIn('not observed inundation', result['potentially_affected_population_interpretation'])

    def test_flood_population_context_keeps_zero_as_valid_result(self):
        geom = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        with patch.object(asset.h, 'raster_population', return_value=0):
            result = asset.flood_population_context(geom, object())
        self.assertEqual(result['potentially_affected_population'], 0)


if __name__ == '__main__':
    unittest.main()
