#!/usr/bin/env python3
"""Build rolling, weekly and monthly Climate Pulse impact/exposure reports.

Reports are derived from the daily reporting ledger, while the 3x-daily enriched
archive remains the raw authority. Spatial deduplication uses the compact
reporting footprints stored in the ledger: event geometries are unioned before
population or crop-area extraction, so repeated observations and overlapping
mapped footprints are not simply added together.

Terminology is intentionally conservative. The headline human metric is
"unique mapped population exposed", not "affected population". Drought crop
area is physical crop area inside mapped drought risk/impact footprints, not
confirmed crop loss.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import rasterio
from pyproj import Geod
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import unary_union

import enrich_hazard_exposure as h
from enrich_asset_exposure import CROP_TIF, raster_area_ha

ROOT = Path(__file__).resolve().parents[1]
DAILY_ROOT = ROOT / "data" / "history" / "daily"
REPORT_DATA_ROOT = ROOT / "data" / "reports"
REPORT_HTML_ROOT = ROOT / "reports"
INDEX_PATH = REPORT_DATA_ROOT / "index.json"
HUMAN_SPATIAL_TYPES = {"Wildfire", "Storm", "Flood"}
GEOD = Geod(ellps="WGS84")
ALERT_RANK = {"": 0, "Green": 1, "Orange": 2, "Red": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_day(value: str | None) -> date:
    return date.fromisoformat(value) if value else utcnow().date()


def period_specs(mode: str, today: date) -> list[dict[str, Any]]:
    if mode == "rolling":
        month_start = today.replace(day=1)
        return [
            {"kind": "rolling", "id": "last-7-days", "label": "Rolling 7 days", "start": today - timedelta(days=6), "end": today, "frozen": False},
            {"kind": "rolling", "id": "month-to-date", "label": "Month to date", "start": month_start, "end": today, "frozen": False},
        ]
    if mode == "previous-week":
        monday = today - timedelta(days=today.weekday())
        end = monday - timedelta(days=1)
        start = end - timedelta(days=6)
        iso_year, iso_week, _ = start.isocalendar()
        return [{"kind": "weekly", "id": f"{iso_year}-W{iso_week:02d}", "label": f"Weekly report · {iso_year}-W{iso_week:02d}", "start": start, "end": end, "frozen": True}]
    if mode == "previous-month":
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = end.replace(day=1)
        return [{"kind": "monthly", "id": start.strftime("%Y-%m"), "label": f"Monthly report · {start.strftime('%B %Y')}", "start": start, "end": end, "frozen": True}]
    raise ValueError(f"unsupported mode: {mode}")


def daily_path(day: date) -> Path:
    return DAILY_ROOT / day.strftime("%Y") / day.strftime("%m") / f"{day.strftime('%d')}.json"


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def safe_geometry(record: dict[str, Any]):
    fp = record.get("reporting_footprint") if isinstance(record.get("reporting_footprint"), dict) else {}
    gj = fp.get("geometry")
    if not isinstance(gj, dict):
        return None
    try:
        g = shape(gj)
        if not g.is_valid:
            g = make_valid(g)
        return g if not g.is_empty else None
    except Exception:  # noqa: BLE001
        return None


def safe_union(geoms: list[Any]):
    clean = [g for g in geoms if g is not None and not g.is_empty]
    if not clean:
        return None
    try:
        out = unary_union(clean)
    except Exception:  # noqa: BLE001
        repaired = []
        for g in clean:
            try:
                gg = make_valid(g)
                if not gg.is_empty:
                    repaired.append(gg)
            except Exception:  # noqa: BLE001
                pass
        if not repaired:
            return None
        out = unary_union(repaired)
    if not out.is_valid:
        out = make_valid(out)
    return out if not out.is_empty else None


def geodesic_area_km2(geom) -> float | None:
    if geom is None or geom.is_empty:
        return None
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(float(area_m2)) / 1_000_000.0
    except Exception:  # noqa: BLE001
        total = 0.0
        for part in getattr(geom, "geoms", []):
            try:
                a, _ = GEOD.geometry_area_perimeter(part)
                total += abs(float(a))
            except Exception:  # noqa: BLE001
                pass
        return total / 1_000_000.0 if total else None


def merge_event(existing: dict[str, Any] | None, record: dict[str, Any], ledger_day: str) -> dict[str, Any]:
    out = deepcopy(existing or {
        "stable_id": record.get("stable_id"),
        "type": record.get("type"),
        "title": record.get("title"),
        "region": record.get("region"),
        "days_observed": [],
        "max_alert_level": "",
        "raw_metrics_max": {},
        "geometries": [],
    })
    out["title"] = record.get("title") or out.get("title")
    out["region"] = record.get("region") or out.get("region")
    if ledger_day not in out["days_observed"]:
        out["days_observed"].append(ledger_day)
    level = str(record.get("max_alert_level") or record.get("alert_level_latest") or "")
    if ALERT_RANK.get(level, 0) >= ALERT_RANK.get(str(out.get("max_alert_level") or ""), 0):
        out["max_alert_level"] = level
    for key, value in (record.get("raw_metrics_max") or {}).items():
        n = finite(value)
        old = finite(out["raw_metrics_max"].get(key))
        if n is not None and (old is None or n > old):
            out["raw_metrics_max"][key] = int(n) if n.is_integer() else n
    geom = safe_geometry(record)
    if geom is not None:
        out["geometries"].append(geom)
    return out


def load_period(start: date, end: date) -> tuple[dict[str, Any], dict[str, Any]]:
    events: dict[str, Any] = {}
    coverage = {"requested_days": (end - start).days + 1, "days_with_ledger": 0, "monitor_runs": 0, "ledger_files": []}
    d = start
    while d <= end:
        path = daily_path(d)
        ledger = load_json(path, None)
        if isinstance(ledger, dict):
            coverage["days_with_ledger"] += 1
            coverage["monitor_runs"] += int(ledger.get("run_count", 0))
            coverage["ledger_files"].append(str(path.relative_to(ROOT)))
            for stable_id, rec in (ledger.get("events") or {}).items():
                if not isinstance(rec, dict):
                    continue
                events[stable_id] = merge_event(events.get(stable_id), rec, d.isoformat())
        d += timedelta(days=1)
    for event in events.values():
        event["geometries"] = [g for g in event["geometries"] if g is not None]
    return events, coverage


def preferred_population_metric(event: dict[str, Any]) -> tuple[str | None, float | None]:
    metrics = event.get("raw_metrics_max") or {}
    typ = event.get("type")
    keys = {
        "Wildfire": ("population_direct", "population_within_5km"),
        "Storm": ("population_wind_39kt", "population_ts_or_impact_footprint", "population_storm_surge"),
        "Flood": ("affected_population", "estimated_population_exposed"),
    }.get(typ, ())
    for key in keys:
        n = finite(metrics.get(key))
        if n is not None:
            return key, n
    return None, None


def event_summaries(events: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stable, event in events.items():
        key, pop = preferred_population_metric(event)
        metrics = event.get("raw_metrics_max") or {}
        rows.append({
            "stable_id": stable,
            "type": event.get("type"),
            "title": event.get("title"),
            "region": event.get("region"),
            "max_alert_level": event.get("max_alert_level"),
            "days_observed": len(event.get("days_observed") or []),
            "preferred_population_metric": key,
            "preferred_population_raw": int(pop) if pop is not None else None,
            "burned_area_ha_max": metrics.get("burned_area_ha"),
            "agricultural_drought_impact_area_km2_max": metrics.get("agricultural_drought_impact_area_km2"),
        })
    rows.sort(key=lambda r: (ALERT_RANK.get(str(r.get("max_alert_level") or ""), 0), r.get("preferred_population_raw") or 0), reverse=True)
    return rows


def compute_metrics(events: dict[str, Any]) -> dict[str, Any]:
    by_type: dict[str, list[Any]] = {}
    merged_event_geom: dict[str, Any] = {}
    mapped_counts = Counter()
    for stable, event in events.items():
        g = safe_union(event.get("geometries") or [])
        if g is not None:
            merged_event_geom[stable] = g
            by_type.setdefault(str(event.get("type")), []).append(g)
            mapped_counts[str(event.get("type"))] += 1

    population = {"combined_raw": None, "by_hazard_raw": {}, "included_hazards": sorted(HUMAN_SPATIAL_TYPES)}
    human_geoms = [g for stable, g in merged_event_geom.items() if events[stable].get("type") in HUMAN_SPATIAL_TYPES]
    pop_path = h.ensure_population_source() if human_geoms else None
    if pop_path:
        with rasterio.open(pop_path) as src:
            combined = safe_union(human_geoms)
            if combined is not None:
                population["combined_raw"] = int(h.raster_population(src, combined))
            for typ in sorted(HUMAN_SPATIAL_TYPES):
                union = safe_union(by_type.get(typ, []))
                if union is not None:
                    population["by_hazard_raw"][typ] = int(h.raster_population(src, union))

    drought_union = safe_union(by_type.get("Drought", []))
    crop_km2 = None
    if drought_union is not None and CROP_TIF.exists():
        crop_ha = raster_area_ha(CROP_TIF, drought_union, 1)
        crop_km2 = None if crop_ha is None else round(float(crop_ha) / 100.0, 1)

    wildfire_union = safe_union(by_type.get("Wildfire", []))
    wildfire_unique_km2 = geodesic_area_km2(wildfire_union)
    wildfire_source_ha = 0.0
    wildfire_source_n = 0
    source_pop_sum = 0.0
    source_pop_n = 0
    for event in events.values():
        metrics = event.get("raw_metrics_max") or {}
        if event.get("type") == "Wildfire":
            n = finite(metrics.get("burned_area_ha"))
            if n is not None:
                wildfire_source_ha += n
                wildfire_source_n += 1
        _, pop = preferred_population_metric(event)
        if pop is not None:
            source_pop_sum += pop
            source_pop_n += 1

    alert_counts = Counter(str(e.get("max_alert_level") or "Unclassified") for e in events.values())
    type_counts = Counter(str(e.get("type") or "Unknown") for e in events.values())
    return {
        "unique_mapped_population_exposed": {
            "raw_value": population["combined_raw"],
            "by_hazard_raw": population["by_hazard_raw"],
            "included_hazards": population["included_hazards"],
            "method": "GHSL 2025 population inside the spatial union of reporting footprints for wildfire, storm and flood; overlapping mapped footprints are counted once",
            "interpretation": "Derived mapped exposure estimate; not source-confirmed affected population or unique individual tracking.",
        },
        "event_deduped_source_population_sum": {
            "raw_value": int(round(source_pop_sum)) if source_pop_n else None,
            "contributing_events": source_pop_n,
            "method": "maximum preferred source/model population metric per stable event, then event-level sum",
            "interpretation": "Same event is counted once, but people shared by different event footprints may still be counted more than once; use unique_mapped_population_exposed for spatial deduplication.",
        },
        "drought_crop_area_within_mapped_risk_footprints_km2": {
            "raw_value": crop_km2,
            "method": "FAO CROPGRIDS 2020 physical crop area inside the spatial union of mapped drought footprints",
            "interpretation": "Spatial crop exposure/context, not confirmed crop damage or crop loss.",
        },
        "wildfire_burned_area": {
            "source_event_deduped_ha": round(wildfire_source_ha) if wildfire_source_n else None,
            "source_contributing_events": wildfire_source_n,
            "mapped_spatial_union_km2": round(wildfire_unique_km2, 1) if wildfire_unique_km2 is not None else None,
        },
        "event_counts": {"total_unique_events": len(events), "by_hazard": dict(sorted(type_counts.items())), "by_max_alert": dict(sorted(alert_counts.items()))},
        "mapped_event_counts_by_hazard": dict(sorted(mapped_counts.items())),
    }


def display_people(value: Any) -> str:
    n = finite(value)
    if n is None:
        return "—"
    if n <= 0:
        return "0 people"
    if n < 1000:
        return "<1,000 people"
    rounded = int(round(n / 1000.0) * 1000)
    return f"≈{rounded:,} people"


def html_escape(value: Any) -> str:
    s = str(value if value is not None else "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_html(report: dict[str, Any]) -> str:
    m = report.get("metrics") or {}
    unique = (m.get("unique_mapped_population_exposed") or {}).get("raw_value")
    crop = (m.get("drought_crop_area_within_mapped_risk_footprints_km2") or {}).get("raw_value")
    fires = m.get("wildfire_burned_area") or {}
    counts = m.get("event_counts") or {}
    coverage = report.get("data_coverage") or {}
    status = "Frozen publication" if report.get("frozen") else "Rolling preview"
    crop_text = "—" if crop is None else f"{float(crop):,.1f} km²"
    fire_text = "—" if fires.get("source_event_deduped_ha") is None else f"{int(fires['source_event_deduped_ha']):,} ha"
    by_hazard = "".join(f"<li><strong>{html_escape(k)}</strong>: {html_escape(v)}</li>" for k, v in (counts.get("by_hazard") or {}).items()) or "<li>No events in available ledger.</li>"
    top = "".join(
        f"<tr><td>{html_escape(e.get('type'))}</td><td>{html_escape(e.get('title'))}</td><td>{html_escape(e.get('region'))}</td><td>{html_escape(e.get('max_alert_level') or '—')}</td><td>{html_escape(display_people(e.get('preferred_population_raw')))}</td></tr>"
        for e in (report.get("events") or [])[:20]
    ) or '<tr><td colspan="5">No events in the available reporting ledger.</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(report['label'])} · Climate Pulse</title>
<style>body{{font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;background:#f4f8fa;color:#203746}}main{{max-width:980px;margin:auto;padding:24px}}a{{color:#17658e}}.hero,.card{{background:#fff;border:1px solid #dce7ed;border-radius:18px;padding:20px;margin-bottom:14px}}.hero h1{{margin:0 0 6px}}.muted{{color:#6c8190}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.metric{{border:1px solid #e1e9ee;border-radius:14px;padding:14px}}.metric b{{display:block;font-size:12px;text-transform:uppercase;color:#6b8190;margin-bottom:6px}}.metric span{{font-size:23px;font-weight:800}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #e6edf1;text-align:left;font-size:13px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}main{{padding:12px}}.metric span{{font-size:19px}}table{{display:block;overflow:auto}}}}</style></head><body><main>
<div class="hero"><a href="../../reports.html">← Reports</a><h1>{html_escape(report['label'])}</h1><div class="muted">{report['period']['start']} to {report['period']['end']} UTC · {status} · generated {html_escape(report['generated_at'])}</div></div>
<div class="grid"><div class="metric"><b>Unique mapped population exposed</b><span>{html_escape(display_people(unique))}</span></div><div class="metric"><b>Drought crop area in mapped risk footprints</b><span>{html_escape(crop_text)}</span></div><div class="metric"><b>Wildfire source burned area · event deduped</b><span>{html_escape(fire_text)}</span></div><div class="metric"><b>Unique events</b><span>{html_escape(counts.get('total_unique_events',0))}</span></div></div>
<div class="card"><h2>Coverage</h2><p>{coverage.get('days_with_ledger',0)} of {coverage.get('requested_days',0)} requested days have a reporting ledger; {coverage.get('monitor_runs',0)} monitoring runs are represented.</p><ul>{by_hazard}</ul></div>
<div class="card"><h2>Event summary</h2><table><thead><tr><th>Hazard</th><th>Event</th><th>Region</th><th>Max alert</th><th>Preferred source/model population</th></tr></thead><tbody>{top}</tbody></table></div>
<div class="card"><h2>Interpretation</h2><p><strong>Unique mapped population exposed</strong> is calculated from the spatial union of available wildfire, storm and flood reporting footprints before extracting GHSL population. It removes geographic overlap in mapped footprints; it is not a verified count of unique affected individuals.</p><p><strong>Drought crop area</strong> is FAO CROPGRIDS physical crop area inside the union of mapped drought risk/impact footprints. It is not confirmed crop loss.</p><p class="muted">Exact raw values are retained in the companion JSON report; webpage population values are rounded to the nearest thousand, and values below 1,000 are shown as &lt;1,000.</p></div>
</main></body></html>"""


def update_index(report: dict[str, Any], html_rel: str, json_rel: str) -> None:
    REPORT_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    idx = load_json(INDEX_PATH, {"schema_version": "1.0", "updated_at": None, "rolling": {}, "weekly": [], "monthly": []})
    item = {
        "id": report["id"], "label": report["label"], "start": report["period"]["start"], "end": report["period"]["end"],
        "generated_at": report["generated_at"], "html": html_rel, "json": json_rel,
    }
    if report["kind"] == "rolling":
        idx.setdefault("rolling", {})[report["id"]] = item
    else:
        key = report["kind"]
        rows = [x for x in (idx.get(key) or []) if x.get("id") != report["id"]]
        rows.append(item)
        rows.sort(key=lambda x: x.get("id", ""), reverse=True)
        idx[key] = rows
    idx["updated_at"] = report["generated_at"]
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_report(spec: dict[str, Any]) -> dict[str, Any]:
    events, coverage = load_period(spec["start"], spec["end"])
    metrics = compute_metrics(events) if events else {
        "unique_mapped_population_exposed": {"raw_value": None, "by_hazard_raw": {}, "included_hazards": sorted(HUMAN_SPATIAL_TYPES)},
        "event_deduped_source_population_sum": {"raw_value": None, "contributing_events": 0},
        "drought_crop_area_within_mapped_risk_footprints_km2": {"raw_value": None},
        "wildfire_burned_area": {"source_event_deduped_ha": None, "source_contributing_events": 0, "mapped_spatial_union_km2": None},
        "event_counts": {"total_unique_events": 0, "by_hazard": {}, "by_max_alert": {}}, "mapped_event_counts_by_hazard": {},
    }
    report = {
        "schema_version": "1.0",
        "kind": spec["kind"], "id": spec["id"], "label": spec["label"], "frozen": bool(spec["frozen"]),
        "generated_at": iso(), "period": {"start": spec["start"].isoformat(), "end": spec["end"].isoformat(), "timezone": "UTC"},
        "data_coverage": coverage, "metrics": metrics, "events": event_summaries(events),
        "authority": {"raw": "data/events/archive (3x-daily enriched snapshots)", "reporting_ledger": "data/history/daily", "population": "JRC GHSL GHS-WUP-POP R2025A epoch 2025", "crops": "FAO CROPGRIDS v1.08 2020"},
        "deduplication": {"event": "stable source identity, preferring GDACS ID when available", "spatial": "union reporting footprints before GHSL/CROPGRIDS extraction"},
    }
    data_dir = REPORT_DATA_ROOT / spec["kind"]
    html_dir = REPORT_HTML_ROOT / spec["kind"]
    data_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / f"{spec['id']}.json"
    html_path = html_dir / f"{spec['id']}.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    update_index(report, str(html_path.relative_to(ROOT)), str(json_path.relative_to(ROOT)))
    return {"kind": spec["kind"], "id": spec["id"], "json": str(json_path.relative_to(ROOT)), "html": str(html_path.relative_to(ROOT)), "events": len(events), "coverage_days": coverage["days_with_ledger"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["rolling", "previous-week", "previous-month"], required=True)
    parser.add_argument("--today", help="UTC date override (YYYY-MM-DD), mainly for reproducible tests")
    args = parser.parse_args()
    today = parse_day(args.today)
    outputs = [write_report(spec) for spec in period_specs(args.mode, today)]
    print(json.dumps({"status": "ok", "mode": args.mode, "outputs": outputs}, indent=2))


if __name__ == "__main__":
    main()
