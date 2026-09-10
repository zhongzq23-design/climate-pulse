import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from check_public_science_copy import (  # noqa: E402
    ROOT,
    check_policy_files,
    is_public_science_page,
    violations,
)


class PublicScienceCopyPolicyTests(unittest.TestCase):
    def test_policy_and_agent_binding_exist(self):
        self.assertEqual(check_policy_files(), [])

    def test_public_page_discovery_rules(self):
        self.assertTrue(is_public_science_page(ROOT / 'methods.html'))
        self.assertTrue(is_public_science_page(ROOT / 'reports.html'))
        self.assertTrue(is_public_science_page(ROOT / 'docs' / 'metric-description.html'))
        self.assertTrue(is_public_science_page(ROOT / 'reports' / 'weekly' / '2026-W37.html'))
        self.assertFalse(is_public_science_page(ROOT / 'assets' / 'app.js'))

    def test_human_review_and_placeholder_markers_fail(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'methods.html'
            path.write_text(
                'AUTHOR_DECISION\nNeeds human review.\nAuthor must confirm.\nTODO\n[citation needed]\n',
                encoding='utf-8',
            )
            got = set(violations(path))
            self.assertIn('AUTHOR_DECISION', got)
            self.assertIn('human-review placeholder', got)
            self.assertIn('author-confirmation placeholder', got)
            self.assertIn('TODO', got)
            self.assertIn('citation-needed placeholder', got)

    def test_clean_public_copy_passes_marker_scan(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'methods.html'
            path.write_text(
                'VPD is calculated as saturation vapour pressure minus actual vapour pressure. '
                'This monthly climate-context metric does not reproduce sub-daily variability.',
                encoding='utf-8',
            )
            self.assertEqual(violations(path), [])


if __name__ == '__main__':
    unittest.main()
