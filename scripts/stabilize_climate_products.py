#!/usr/bin/env python3
"""Restore tracked climate products when only top-level generated_at changed.

Climate context is intentionally recomputed when the refresh gate says its
scientific inputs may have changed.  A successful recomputation can nevertheless
produce byte-level diffs solely because ``generated_at`` reflects execution time.
Those timestamp-only rewrites create very large Git commits and unnecessary
public-site synchronization.

This script compares the just-generated JSON products with the version at HEAD.
If the parsed documents are identical after removing only the top-level
``generated_at`` field, the exact tracked bytes are restored.  Any scientific,
provenance, coverage, event-universe, source-window or value change is retained.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLIMATE_DIR = ROOT / "data" / "climate" / "event_timeseries"


def load_text_json(text: str) -> Any:
    return json.loads(text)


def without_generated_at(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    out = dict(value)
    out.pop("generated_at", None)
    return out


def tracked_text(path: Path) -> str | None:
    rel = path.relative_to(ROOT).as_posix()
    proc = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.stdout if proc.returncode == 0 else None


def stabilize(path: Path) -> bool:
    old_text = tracked_text(path)
    if old_text is None or not path.exists():
        return False
    try:
        old = load_text_json(old_text)
        new = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if without_generated_at(old) != without_generated_at(new):
        return False
    current_text = path.read_text(encoding="utf-8")
    if current_text == old_text:
        return False
    path.write_text(old_text, encoding="utf-8")
    return True


def main() -> int:
    if not CLIMATE_DIR.exists():
        print(json.dumps({"status": "skip", "reason": "climate_dir_missing"}))
        return 0

    targets = sorted(path for path in CLIMATE_DIR.glob("*.json") if path.is_file())
    restored = [path.relative_to(ROOT).as_posix() for path in targets if stabilize(path)]
    print(
        json.dumps(
            {
                "status": "ok",
                "checked": len(targets),
                "timestamp_only_products_restored": len(restored),
                "restored": restored,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
