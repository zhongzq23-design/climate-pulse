#!/usr/bin/env python3
"""Build the code-free public Climate Pulse website artifact.

The private backend is the authority for ingestion, GEE/Python processing, QC and
report generation. This exporter copies only explicitly approved website/output
paths into ``public-dist``. It intentionally excludes backend implementation,
tests, operational lessons, migration material, private workflows, and the
public repository's GitHub Pages workflow. The Pages workflow is maintained
separately in the public repository so the cross-repository publisher token only
needs Contents read/write permission and never needs workflow-management scope.
"""
from __future__ import annotations

import argparse
import json
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

PUBLIC_README = """# Climate Pulse\n\nPublic website and generated output artifacts for Climate Pulse.\n\nThe operational backend, Python/GEE processing code, quality-control logic and\nscheduled data-processing workflows are maintained in a separate private\nrepository. Public scientific methods and generated event/report data remain\navailable here for transparency and reproducibility of interpretation.\n"""


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


def assert_text_only_climate_context(out_root: Path) -> None:
    """Keep the user-approved climate background presentation text-only.

    The private backend is the publishing authority.  A previous public-only
    rollback was overwritten by the next scheduled publish because the backend
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
