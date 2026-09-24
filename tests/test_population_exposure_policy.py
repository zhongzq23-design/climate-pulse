import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from enforce_population_exposure_policy import (  # noqa: E402
    canonical_geometry_semantics,
    strict_tc_wind_method,
)
from update_daily_ledger import geometry_semantics  # noqa: E402


PASS_WILDFIRE_QC = {
    'status': 'pass',
    'semantic_role': 'burned_area_perimeter',
    'exposure_grade_eligible': True,
    'source_authority_verified': True,
    'episode_aligned': True,
}


class PopulationExposurePolicyTests(unittest.TestCase):
    def test_headline_geometry_grades_by_hazard(self):
        self.assertEqual(
            geometry_semantics(
                'Wildfire',
                'gdacs_gwis_episode_aligned_burned_area_perimeter_polygon',
                PASS_WILDFIRE_QC,
            )['grade'],
            'exposure_grade',
        )
        self.assertEqual(
            geometry_semantics('Wildfire', 'GDACS burned-area/event polygon')['grade'],
            'context_grade',
        )
        self.assertEqual(geometry_semantics('Storm', 'gdacs_wind_polygon')['grade'], 'context_grade')
        self.assertEqual(
            geometry_semantics('Storm', 'gdacs_observed_wind_34kt_polygon')['grade'],
            'exposure_grade',
        )
        self.assertEqual(geometry_semantics('Flood', 'GDACS flood event polygon')['grade'], 'context_grade')
        self.assertEqual(geometry_semantics('Drought', 'GDACS drought event polygon')['grade'], 'risk_grade')

    def test_wildfire_requires_explicit_admission_qc(self):
        passed = canonical_geometry_semantics(
            'Wildfire',
            'gdacs_gwis_episode_aligned_burned_area_perimeter_polygon',
            PASS_WILDFIRE_QC,
        )
        missing = canonical_geometry_semantics('Wildfire', 'GDACS burned-area/event polygon', None)
        failed = canonical_geometry_semantics(
            'Wildfire',
            'gdacs_wildfire_event_polygon_unverified',
            {**PASS_WILDFIRE_QC, 'status': 'failed', 'exposure_grade_eligible': False},
        )
        self.assertEqual(passed['grade'], 'exposure_grade')
        self.assertTrue(passed['suitable_for_cross_hazard_population'])
        self.assertEqual(missing['grade'], 'context_grade')
        self.assertFalse(missing['suitable_for_cross_hazard_population'])
        self.assertEqual(failed['grade'], 'context_grade')
        self.assertFalse(failed['suitable_for_cross_hazard_population'])

    def test_tc_requires_threshold_and_observed_semantics(self):
        for method in (
            'gdacs_observed_wind_34kt_polygon',
            'gdacs_actual_wind_64kt_polygon',
            'GDACS observed wind 34 knots polygon',
        ):
            ok, threshold = strict_tc_wind_method(method)
            self.assertTrue(ok, method)
            self.assertIn(threshold, {'34 kt', '64 kt'})
            sem = canonical_geometry_semantics('Storm', method)
            self.assertEqual(sem['grade'], 'exposure_grade')
            self.assertTrue(sem['suitable_for_cross_hazard_population'])

    def test_tc_fails_closed_on_generic_forecast_or_unverified_geometry(self):
        for method in (
            'gdacs_wind_polygon',
            'gdacs_event_polygon_fallback',
            'gdacs_forecast_wind_34kt_polygon',
            'gdacs_observed_wind_polygon',
            'gdacs_wind_34kt_polygon',
            'gdacs_wind_labelled_polygon_unverified_threshold',
            '300_km_point_population_fallback',
            None,
        ):
            sem = canonical_geometry_semantics('Storm', method)
            self.assertEqual(sem['grade'], 'context_grade', method)
            self.assertFalse(sem['suitable_for_cross_hazard_population'], method)

    def test_canonical_policy_excludes_non_primary_grades_from_headline(self):
        text = (ROOT / 'docs' / 'POPULATION_EXPOSURE_STANDARD.md').read_text(encoding='utf-8')
        self.assertIn('context-grade, proximity-grade, and risk-grade footprints are excluded', text)
        self.assertIn('does **not** use one universal distance radius', text)
        self.assertIn('The 5 km metric is **not** part of the primary cross-hazard population headline.', text)
        self.assertIn('Population Exposure Standard v1.2', text)
        self.assertIn('34 kt', text)
        self.assertIn('64 kt', text)
        self.assertIn('same-episode', text)
        self.assertIn('0.5', text)
        self.assertIn('2.0', text)

    def test_flood_reported_area_remains_context_not_inundation(self):
        reporting = (ROOT / 'REPORTING.md').read_text(encoding='utf-8')
        methods = (ROOT / 'methods.html').read_text(encoding='utf-8')
        self.assertIn('GDACS flood reported affected/event areas', reporting)
        self.assertIn('not observed inundation extent', reporting)
        self.assertIn('Current implementation:', methods)
        self.assertIn('context-grade', methods)
        self.assertIn('aligned to the reported event coordinate', methods)
        self.assertIn('75 km', methods)
        self.assertIn('does not establish inundation', methods)

    def test_public_methods_keep_exposure_distinct_from_affected(self):
        methods = (ROOT / 'methods.html').read_text(encoding='utf-8')
        self.assertIn('Exposure does not imply observed harm.', methods)
        self.assertIn('Used only when a source reports an actual impact.', methods)
        self.assertIn('spatially deduplicated mapped exposure, not verified tracking of unique affected individuals', methods)

    def test_public_methods_describe_strict_tc_gate(self):
        methods = (ROOT / 'methods.html').read_text(encoding='utf-8')
        self.assertIn('34 kt', methods)
        self.assertIn('64 kt', methods)
        self.assertIn('observed/actual', methods)
        self.assertIn('generic wind-labelled polygon', methods)
        self.assertIn('excluded from the cross-hazard headline', methods)

    def test_public_methods_describe_wildfire_admission_gate(self):
        methods = (ROOT / 'methods.html').read_text(encoding='utf-8')
        self.assertIn('same GDACS/GWIS episode', methods)
        self.assertIn('coarse area-integrity check', methods)
        self.assertIn('not an uncertainty interval', methods)

    def test_public_tc_ui_does_not_publish_legacy_field_suffixes_as_knot_thresholds(self):
        ui = (ROOT / 'assets' / 'hazard-exposure-ui.js').read_text(encoding='utf-8')
        self.assertNotIn('≥39 kt wind field', ui)
        self.assertNotIn('≥74 kt wind field', ui)
        self.assertIn('raw field pop39', ui)
        self.assertIn('documented method uses 34 kt', ui)
        self.assertIn('raw field pop74', ui)
        self.assertIn('documented method uses 64 kt', ui)
        self.assertIn('context/screening; not headline exposure', ui)


if __name__ == '__main__':
    unittest.main()
