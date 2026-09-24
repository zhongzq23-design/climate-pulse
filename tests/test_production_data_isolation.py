#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.production_data_branch as pdb


class ProductionDataIsolationTests(unittest.TestCase):
    def test_rejects_unapproved_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not authorized"):
            pdb.normalize_rel(".github/workflows/evil.yml")
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            pdb.normalize_rel("../escape")

    def test_store_rejects_executable_top_level_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / "data").mkdir()
            (store / ".github").mkdir()
            with self.assertRaisesRegex(RuntimeError, "unexpected top-level"):
                pdb.validate_store_boundary(store)

    def test_restore_replaces_authorized_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            store = base / "store"
            (root / "data/events").mkdir(parents=True)
            (store / "data/events").mkdir(parents=True)
            (root / "data/events/latest.json").write_text("old", encoding="utf-8")
            (store / "data/events/latest.json").write_text("new", encoding="utf-8")
            (store / "README.md").write_text("data-only", encoding="utf-8")
            with patch.object(pdb, "ROOT", root):
                pdb.sync("restore", store, [Path("data/events")])
            self.assertEqual(
                (root / "data/events/latest.json").read_text(encoding="utf-8"),
                "new",
            )

    def test_writer_workflows_do_not_push_main(self) -> None:
        writer_paths = [
            ".github/workflows/monitor-events.yml",
            ".github/workflows/periodic-reports.yml",
            ".github/workflows/prepare-cropgrids-2020.yml",
            ".github/workflows/prepare-cru-annual.yml",
            ".github/workflows/prepare-cru-monthly-context.yml",
            ".github/workflows/prepare-modis-landcover-2024.yml",
            ".github/workflows/prepare-population.yml",
        ]
        for rel in writer_paths:
            text = (pdb.ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("production-data", text, rel)
            self.assertIn("working-directory: .production-data", text, rel)
            self.assertNotIn("git push origin main", text, rel)
            self.assertNotIn("git push origin HEAD:main", text, rel)

    def test_secret_jobs_reference_production_environment(self) -> None:
        for rel in (
            ".github/workflows/monitor-events.yml",
            ".github/workflows/secret-climate-validation.yml",
            ".github/workflows/prepare-modis-landcover-2024.yml",
        ):
            text = (pdb.ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("environment: production-secrets", text, rel)


if __name__ == "__main__":
    unittest.main()
