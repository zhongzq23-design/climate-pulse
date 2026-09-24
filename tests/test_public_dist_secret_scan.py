#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_public_dist import assert_no_embedded_secrets


class PublicDistSecretScanTests(unittest.TestCase):
    def run_scan(self, files: dict[str, str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, content in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            assert_no_embedded_secrets(root)

    def test_allows_benign_placeholders(self) -> None:
        self.run_scan(
            {
                "index.html": "Authorization: Bearer ${token}",
                "data/example.json": json.dumps(
                    {"type": "service_" + "account", "note": "no private key is present"}
                ),
            }
        )

    def test_rejects_service_account_private_key(self) -> None:
        pem = "-----BEGIN " + "PRIVATE KEY-----\\nnot-a-real-key\\n-----END " + "PRIVATE KEY-----"
        payload = json.dumps(
            {
                "type": "service_" + "account",
                "private_" + "key": pem,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "secret scan failed"):
            self.run_scan({"data/leak.json": payload})

    def test_rejects_github_pat_shape(self) -> None:
        fake = "gh" + "p_" + ("A" * 36)
        with self.assertRaisesRegex(RuntimeError, "GitHub token"):
            self.run_scan({"assets/leak.txt": fake})

    def test_rejects_google_api_key_shape(self) -> None:
        fake = "AI" + "za" + ("A" * 35)
        with self.assertRaisesRegex(RuntimeError, "Google API key"):
            self.run_scan({"assets/leak.js": fake})


if __name__ == "__main__":
    unittest.main()
