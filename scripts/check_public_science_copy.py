#!/usr/bin/env python3
"""Deterministic publication gate for Climate Pulse public scientific copy.

The substantive editorial pass is governed by docs/PUBLIC_SCIENCE_WRITING_RULES.md
and performed by GPT/project agents before publication. This script provides the
part that can be checked deterministically in CI: the policy must exist and
public scientific pages must not contain unresolved human-review/editorial
placeholders.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "PUBLIC_SCIENCE_WRITING_RULES.md"
AGENTS = ROOT / "AGENTS.md"

PUBLIC_STEM_TOKENS = (
    "method",
    "methodology",
    "definition",
    "description",
    "explainer",
    "report",
)
ALWAYS_PUBLIC = {"index.html", "methods.html", "reports.html"}

BLOCKED = (
    ("AUTHOR_DECISION", re.compile(r"\bAUTHOR_DECISION\b", re.I)),
    ("EVIDENCE_REQUIRED", re.compile(r"\bEVIDENCE_REQUIRED\b", re.I)),
    ("human-review placeholder", re.compile(r"\b(?:needs?|requires?)\s+human\s+(?:scientific\s+)?review\b", re.I)),
    ("author-confirmation placeholder", re.compile(r"\b(?:author\s+must\s+confirm|needs?\s+author\s+confirmation|ask\s+the\s+author)\b", re.I)),
    ("TODO", re.compile(r"\bTODO\b", re.I)),
    ("TBD", re.compile(r"\bTBD\b", re.I)),
    ("XXX placeholder", re.compile(r"\bX{3,}\b", re.I)),
    ("citation-needed placeholder", re.compile(r"\[\s*citation\s+needed\s*\]", re.I)),
    ("insert placeholder", re.compile(r"(?:\[|<)\s*insert\b", re.I)),
)


def is_public_science_page(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    if rel.as_posix() in ALWAYS_PUBLIC:
        return True
    if path.suffix.lower() != ".html":
        return False
    if rel.parts and rel.parts[0] not in {"docs", "reports"} and len(rel.parts) > 1:
        return False
    stem = path.stem.lower()
    return any(token in stem for token in PUBLIC_STEM_TOKENS) or (rel.parts and rel.parts[0] == "reports")


def discover_pages() -> list[Path]:
    pages: set[Path] = set()
    for name in ALWAYS_PUBLIC:
        path = ROOT / name
        if path.exists():
            pages.add(path)
    for base_name in ("docs", "reports"):
        base = ROOT / base_name
        if base.exists():
            for path in base.rglob("*.html"):
                if is_public_science_page(path):
                    pages.add(path)
    for path in ROOT.glob("*.html"):
        if is_public_science_page(path):
            pages.add(path)
    return sorted(pages)


def violations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [label for label, pattern in BLOCKED if pattern.search(text)]


def check_policy_files() -> list[str]:
    errors: list[str] = []
    if not POLICY.exists():
        errors.append("missing docs/PUBLIC_SCIENCE_WRITING_RULES.md")
    else:
        text = POLICY.read_text(encoding="utf-8", errors="replace")
        for required in ("Scientific meaning firewall", "autonomous GPT resolution", "SCFL editorial pass", "Final invariant audit"):
            if required.lower() not in text.lower():
                errors.append(f"writing policy missing required section: {required}")
    if not AGENTS.exists():
        errors.append("missing AGENTS.md")
    else:
        text = AGENTS.read_text(encoding="utf-8", errors="replace")
        if "PUBLIC_SCIENCE_WRITING_RULES.md" not in text:
            errors.append("AGENTS.md does not bind agents to the public science writing policy")
    return errors


def run(paths: Iterable[Path] | None = None) -> list[str]:
    errors = check_policy_files()
    selected = list(paths) if paths is not None else discover_pages()
    for path in selected:
        if not path.exists() or not is_public_science_page(path):
            continue
        for label in violations(path):
            errors.append(f"{path.relative_to(ROOT)}: unresolved public-copy marker: {label}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Optional repository-relative HTML paths to check")
    args = parser.parse_args()
    paths = [ROOT / p for p in args.paths] if args.paths else None
    errors = run(paths)
    if errors:
        print("Public science copy policy check: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    checked = paths or discover_pages()
    print(f"Public science copy policy check: PASS ({len(checked)} page(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
