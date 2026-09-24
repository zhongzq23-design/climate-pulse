from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"


class WorkflowSecurityTests(unittest.TestCase):
    def _texts(self):
        for root in (WORKFLOWS, SCRIPTS):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".yml", ".yaml", ".py", ".sh", ".ps1"}:
                    yield path, path.read_text(encoding="utf-8")

    def test_no_retired_cross_repo_token_dependency(self):
        offenders = [str(p.relative_to(ROOT)) for p, text in self._texts() if "PUBLIC_REPO_TOKEN" in text]
        self.assertEqual(offenders, [])

    def test_no_pull_request_target(self):
        offenders = [str(p.relative_to(ROOT)) for p, text in self._texts() if "pull_request_target" in text]
        self.assertEqual(offenders, [])

    def test_secret_workflows_are_not_pr_workflows(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            if "${{ secrets." in text and re.search(r"(?m)^\s*pull_request\s*:", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_no_obvious_environment_or_secret_log_dump(self):
        checks = (
            re.compile(r"(?m)^\s*printenv(?:\s|$)"),
            re.compile(r"(?m)^\s*env\s*(?:$|[|>])"),
            re.compile(r"(?m)^\s*(?:set\s+-x|set\s+-o\s+xtrace)\b"),
            re.compile(r"(?:Get-ChildItem|gci|dir)\s+Env:", re.I),
            re.compile(r"(?:print|pprint)\s*\(\s*(?:dict\s*\(\s*)?os\.environ", re.I),
        )
        offenders = []
        for path, text in self._texts():
            if any(rx.search(text) for rx in checks):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
