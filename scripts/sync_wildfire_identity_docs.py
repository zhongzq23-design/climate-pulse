#!/usr/bin/env python3
"""Keep user-facing wildfire identity wording aligned with the runtime policy."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"expected policy text not found in {path.relative_to(ROOT)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def main() -> None:
    methods_old = '''      <article class="method-card">\n        <h2>3. Deduplication and map clustering</h2>\n        <p>Source IDs and links are retained. Obvious cross-source duplicates may be merged when they describe the same hazard type and occur close together in space and time.</p>\n        <div class="formula">candidate duplicate if: same hazard type AND distance &lt; 80 km AND |date₁ − date₂| ≤ 5 days</div>\n        <p>Major wildfire records may also be grouped visually when they occur within roughly 180 km and 7 days. A grouped marker is a display object rather than one source polygon. Member sums may overlap and are never labelled as unique-population totals.</p>\n      </article>'''
    methods_new = '''      <article class="method-card">\n        <h2>3. Deduplication and wildfire identity</h2>\n        <p>Source IDs and links are retained. For non-wildfire hazards, obvious cross-source duplicates may still be merged when they describe the same hazard type and occur close together in space and time.</p>\n        <div class="formula">non-wildfire candidate duplicate if: same hazard type AND distance &lt; 80 km AND |date₁ − date₂| ≤ 5 days</div>\n        <p><strong>Wildfires are not merged or clustered merely because they are nearby.</strong> Distinct GDACS wildfire event IDs are published as separate events, even when their locations and dates are close. Only an explicit shared source identity, such as the same source ID or a CEMS record that directly references the same GDACS ID, may be deduplicated as the same wildfire.</p>\n      </article>'''

    reporting_old = '''For screened hazards such as wildfire and tropical cyclone, legacy ledger records without a persisted display decision fail closed and are not promoted to the significant headline universe.\n'''
    reporting_new = reporting_old + '''\n### Wildfire event identity\n\nDistinct wildfire source IDs remain distinct stable events. Spatial/temporal proximity alone never merges wildfire records and the public map does not create regional wildfire cluster markers. An explicit source identity/link may still deduplicate two records that demonstrably refer to the same wildfire.\n'''

    changed = []
    if replace_once(ROOT / "methods.html", methods_old, methods_new):
        changed.append("methods.html")
    if replace_once(ROOT / "REPORTING.md", reporting_old, reporting_new):
        changed.append("REPORTING.md")
    print("updated:" if changed else "already current:", ", ".join(changed) if changed else "wildfire identity docs")


if __name__ == "__main__":
    main()
