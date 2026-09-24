#!/usr/bin/env python3
"""Build the browser-facing Climate Pulse Pages artifact.

The public repository is the operational source repository and contains both
backend processing code and website content.  GitHub Pages must still publish
only an explicitly approved browser-facing subset.  This builder creates that
subset in `public-dist` and excludes processing code, tests, workflows,
operational lessons and credential-like material.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "public-dist"

PUBLIC_PATHS = (
    Path(".nojekyll"),
    Path("index.html"),
    Path("methods.html"),
    Path("reports.html"),
    Path("assets"),
    Path("data"),
    Path("reports"),
    Path("docs/POPULATION_EXPOSURE_STANDARD.md"),
)

FORBIDDEN_SUFFIXES = {".py", ".pyc", ".ipynb", ".sh", ".ps1"}
FORBIDDEN_PARTS = {
    "scripts", "tests", "gee", "migration", "__pycache__", ".runtime",
}
FORBIDDEN_NAME_TOKENS = {
    "credential", "credentials", "secret", "secrets", "service-account",
    "service_account", ".env",
}

SECRET_SCAN_SUFFIXES = {
    ".html", ".htm", ".js", ".mjs", ".css", ".json", ".geojson", ".txt",
    ".md", ".csv", ".tsv", ".xml", ".svg", ".map",
}
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("PEM private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Google OAuth token", re.compile(rb"\bya29\.[0-9A-Za-z_-]{20,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Bearer token", re.compile(rb"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{20,}\b")),
)
SERVICE_ACCOUNT_TYPE_RE = re.compile(rb'"type"\s*:\s*"service_account"')
SERVICE_ACCOUNT_PRIVATE_KEY_RE = re.compile(rb'"private_key"\s*:')

PUBLIC_README = """# Climate Pulse\n\nBrowser-facing Climate Pulse site artifact generated from the public operational source repository.\n\nProcessing code, tests, CI workflows and secrets are intentionally excluded from the GitHub Pages artifact even though the source repository itself is public.\n"""


def copy_path(src_rel: Path, out_root: Path) -> None:
    src = ROOT / src_rel
    if not src.exists():
        raise RuntimeError(f"Required public artifact path is missing: {src_rel}")
    dst = out_root / src_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def assert_public_boundary(out_root: Path) -> None:
    violations: list[str] = []
    for path in out_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(out_root)
        lower_parts = {part.lower() for part in rel.parts}
        name = rel.name.lower()
        if rel.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"backend-code suffix: {rel}")
        if lower_parts & FORBIDDEN_PARTS:
            violations.append(f"forbidden private path: {rel}")
        if any(token in name for token in FORBIDDEN_NAME_TOKENS):
            violations.append(f"credential-like filename: {rel}")
        if rel.suffix.lower() in {".yml", ".yaml"}:
            violations.append(f"workflow/config YAML is managed outside the publisher artifact: {rel}")
    if violations:
        raise RuntimeError("Public artifact boundary failed:\n" + "\n".join(sorted(set(violations))))


def assert_no_embedded_secrets(out_root: Path) -> None:
    """Fail closed if the browser-facing artifact contains credential material."""
    violations: list[str] = []
    scanned_files = 0
    for path in out_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SECRET_SCAN_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            continue

        scanned_files += 1
        rel = path.relative_to(out_root)
        if SERVICE_ACCOUNT_TYPE_RE.search(data) and SERVICE_ACCOUNT_PRIVATE_KEY_RE.search(data):
            violations.append(f"Google service-account JSON: {rel}")
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                violations.append(f"{label}: {rel}")

    if violations:
        raise RuntimeError(
            "Public artifact secret scan failed:\n"
            + "\n".join(sorted(set(violations)))
        )
    print(f"Public artifact secret scan passed: {scanned_files} text-like files checked")


def assert_text_only_climate_context(out_root: Path) -> None:
    """Keep the user-approved climate background presentation text-only.

    The public repository is now the source and execution authority. A previous
    presentation rollback was overwritten by a later automated refresh because the source
    still contained an older mini-chart implementation.  Treat the absence of
    chart rendering as a publication contract so that this cannot silently
    regress again.
    """
    js_path = out_root / "assets/climate-context-ui.js"
    css_path = out_root / "assets/climate-context.css"
    if not js_path.is_file() or not css_path.is_file():
        raise RuntimeError("Text-only climate contract failed: climate UI JS/CSS is missing")

    js = js_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8")
    forbidden = {
        "assets/climate-context-ui.js": (
            "miniBarChart(",
            "comparison-chart-wrap",
            "climate-mini-bar",
            "<svg",
        ),
        "assets/climate-context.css": (
            ".comparison-chart",
            ".comparison-chart-wrap",
            ".climate-mini-bar",
            ".climate-mini-zero",
        ),
    }
    violations = [
        f"{path}: {marker}"
        for path, markers in forbidden.items()
        for marker in markers
        if marker in (js if path.endswith(".js") else css)
    ]
    if violations:
        raise RuntimeError(
            "Text-only climate contract failed; chart rendering was reintroduced:\n"
            + "\n".join(violations)
        )
    print("Text-only climate context contract passed: no chart/SVG rendering markers")


def assert_browser_snapshot_contract(out_root: Path) -> None:
    """Verify that the published browser loader can consume the published event schema.

    This is intentionally a small structural contract rather than an exact-source
    text comparison. It prevents a healthy-looking live-feed fallback from hiding
    loss of repository-only enrichments such as mapped event footprints.
    """
    latest_path = out_root / "data/events/latest.json"
    sources_path = out_root / "assets/sources.js"
    if not latest_path.is_file() or not sources_path.is_file():
        raise RuntimeError("Browser snapshot contract failed: latest.json or assets/sources.js is missing")

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    canonical = latest.get("canonical_events")
    legacy = latest.get("events")
    if isinstance(canonical, list) and canonical:
        published_events = canonical
        required_markers = ("j.canonical_events", "events: snapshotEvents")
    elif isinstance(legacy, list) and legacy:
        published_events = legacy
        required_markers = ("j.events",)
    else:
        raise RuntimeError("Browser snapshot contract failed: no non-empty canonical_events/events array")

    source_js = sources_path.read_text(encoding="utf-8")
    missing_markers = [marker for marker in required_markers if marker not in source_js]
    if missing_markers:
        raise RuntimeError(
            "Browser snapshot contract failed: assets/sources.js does not support the published event schema; "
            f"missing markers {missing_markers}"
        )

    missing_footprints: list[str] = []
    ready_count = 0
    for event in published_events:
        if not isinstance(event, dict):
            continue
        footprint = event.get("footprint")
        if not isinstance(footprint, dict) or footprint.get("status") != "ready":
            continue
        rel = footprint.get("path")
        if not isinstance(rel, str) or not rel.strip():
            missing_footprints.append(f"{event.get('id', '<unknown>')}: missing footprint path")
            continue
        ready_count += 1
        target = out_root / rel
        try:
            target.relative_to(out_root)
        except ValueError:
            missing_footprints.append(f"{event.get('id', '<unknown>')}: footprint path escapes public root: {rel}")
            continue
        if not target.is_file():
            missing_footprints.append(f"{event.get('id', '<unknown>')}: referenced footprint not published: {rel}")

    if missing_footprints:
        raise RuntimeError(
            "Browser snapshot contract failed for ready footprint references:\n"
            + "\n".join(missing_footprints[:50])
        )

    print(
        "Browser snapshot contract passed: "
        f"{len(published_events)} events; {ready_count} ready footprint references verified"
    )


def build(out_root: Path) -> None:
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    for rel in PUBLIC_PATHS:
        copy_path(rel, out_root)

    (out_root / "README.md").write_text(PUBLIC_README, encoding="utf-8")
    assert_public_boundary(out_root)
    assert_no_embedded_secrets(out_root)
    assert_text_only_climate_context(out_root)
    assert_browser_snapshot_contract(out_root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="build and validate the public artifact boundary")
    parser.add_argument("--output", default=str(DIST), help="output directory")
    args = parser.parse_args()
    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out
    build(out)
    files = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Public artifact build passed: {files} files in {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main()
