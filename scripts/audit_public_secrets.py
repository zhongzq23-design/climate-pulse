#!/usr/bin/env python3
"""Fail closed on credential-like material in the public repository worktree."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".production-data", "public-dist", "__pycache__"}
TEXT_SUFFIXES = {".py", ".yml", ".yaml", ".json", ".geojson", ".md", ".html", ".js", ".css", ".txt", ".csv", ".toml", ".ini", ".cfg", ".sh", ".ps1"}
PATTERNS = {
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "google_api_key": re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "json_private_key": re.compile(r'"private_key"\\s*:\\s*"-----BEGIN'),
    "bearer_token": re.compile(r"Bearer\\s+[A-Za-z0-9._~+/=-]{24,}", re.I),
    "secret_assignment": re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\\s*[=:]\\s*[\'\\\"][^\'\\\"]{16,}[\'\\\"]"),
}
ALLOW_MARKERS = ("secrets.", "os.environ.get(", "os.getenv(")
hits = []
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                if name == "secret_assignment" and any(m in line for m in ALLOW_MARKERS):
                    continue
                hits.append((str(path.relative_to(ROOT)), lineno, name))
if hits:
    for path, lineno, name in hits:
        print(f"POTENTIAL_SECRET {name} {path}:{lineno}")
    raise SystemExit(1)
print("PUBLIC_REPOSITORY_SECRET_AUDIT=PASS")
