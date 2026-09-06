#!/usr/bin/env python3
"""Build Climate Pulse rolling, weekly and monthly reports with integrity gates.

Headline statistics use public/significant events. Geometry semantics determine
which mapped areas can contribute to cross-hazard human exposure: wildfire and
cyclone exposure-grade footprints may be combined, while GDACS flood event areas
remain context-grade and drought polygons remain risk-grade. Frozen weekly/monthly
publications are withheld unless every requested UTC day has a daily ledger.
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
GEOD = Geod(ellps="WGS84")
ALERT_RANK = {"": 0, "Green": 1, "Orange": 2, "Red": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_day(value: str | None) -> date:
    return date.fromisoformat(value) if value else utcnow().date()


def parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def period_specs(mode: str, today: date) -> list[dict[str, Any]]:
    if mode == "rolling":
        return [
            {"kind": "rolling", "id": "last-7-days", "label": "Rolling 7 days", "start": today - timedelta(days=6), "end": today, "frozen": False},
            {"kind": "rolling", "id": "month-to-date", "label": "Month to date", "start": today.replace(day=1), "end": today, "frozen": False},
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


def geometry_semantics(record: dict[str, Any]) -> dict[str, Any]:
    fp = record.get("reporting_footprint") if isinstance(record.get("reporting_footprint"), dict) else {}
    sem = fp.get("geometry_semantics") if isinstance(fp.get("geometry_semantics"), dict) else {}
    if sem.get("grade"):
        return sem
    typ = str(record.get("type") or "")
    if typ == "Wildfire":
        return {"grade": "exposure_grade", "role": "burned_fire_perimeter"}
    if typ == "Storm":
        return {"grade": "exposure_grade", "role": "tropical_cyclone_hazard_zone"}
    if typ == "Flood":
        return {"grade": "context_grade", "role": "reported_flood_event_area"}
    if typ == "Drought":
        return {"grade": "risk_grade", "role": "drought_risk_impact_area"}
    return {"grade": "context_grade", "role": "mapped_event_context"}


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


def min_time(*values: Any) -> str | None:
    rows = [(parse_dt(v), str(v)) for v in values if v]
    rows = [(d, s) for d, s in rows if d]
    return min(rows, key=lambda x: x[0])[1] if rows else None


def max_time(*values: Any) -> str | None:
    rows = [(parse_dt(v), str(v)) for v in values if v]
    rows = [(d, s) for d, s in rows if d]
    return max(rows, key=lambda x: x[0])[1] if rows else None


def record_public_significant(record: dict[str, Any]) -> bool:
    if "public_significant" in record:
        return bool(record.get("public_significant"))
    if "public_significant_latest" in record:
        return bool(record.get("public_significant_latest"))
    return str(record.get("type") or "") not in {"Wildfire", "Storm"}


def merge_event(existing: dict[str, Any] | None, record: dict[str, Any], ledger_day: str) -> dict[str, Any]:
    out = deepcopy(existing or {
        "stable_id": record.get("stable_id"), "type": record.get("type"), "title": record.get("title"), "region": record.get("region"),
        "days_observed": [], "max_alert_level": "", "raw_metrics_max": {}, "raw_metrics_latest": {}, "geometry_records": [],
        "public_significant": False,
    })
    out["title"] = record.get("title") or out.get("title")
    out["region"] = record.get("region") or out.get("region")
    if ledger_day not in out["days_observed"]:
        out["days_observed"].append(ledger_day)
    out["days_observed"].sort()
    out["public_significant"] = bool(out.get("public_significant")) or record_public_significant(record)
    out["lifecycle_first_seen"] = min_time(out.get("lifecycle_first_seen"), record.get("lifecycle_first_seen"), record.get("first_observed_at"))
    out["lifecycle_last_seen"] = max_time(out.get("lifecycle_last_seen"), record.get("lifecycle_last_seen"), record.get("last_observed_at"))
    level = str(record.get("max_alert_level") or record.get("alert_level_latest") or "")
    if ALERT_RANK.get(level, 0) >= ALERT_RANK.get(str(out.get("max_alert_level") or ""), 0):
        out["max_alert_level"] = level
    for key, value in (record.get("raw_metrics_max") or {}).items():
        n = finite(value)
        old = finite(out["raw_metrics_max"].get(key))
        if n is not None and (old is None or n > old):
            out["raw_metrics_max"][key] = int(n) if n.is_integer() else n
    observed = parse_dt(record.get("last_observed_at"))
    previous_observed = parse_dt(out.get("latest_observed_at"))
    if observed and (previous_observed is None or observed >= previous_observed):
        out["latest_observed_at"] = record.get("last_observed_at")
        out["raw_metrics_latest"] = deepcopy(record.get("raw_metrics_latest") or {})
        out["alert_level_latest"] = record.get("alert_level_latest")
        out["temporal_latest"] = deepcopy(record.get("temporal_latest") or {})
    g = safe_geometry(record)
    if g is not None:
        sem = geometry_semantics(record)
        out["geometry_records"].append({"day": ledger_day, "geometry": g, "grade": sem.get("grade"), "role": sem.get("role")})
    return out


def load_period(start: date, end: date) -> tuple[dict[str, Any], dict[str, Any]]:
    events: dict[str, Any] = {}
    missing: list[str] = []
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
                if isinstance(rec, dict):
                    events[stable_id] = merge_event(events.get(stable_id), rec, d.isoformat())
        else:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    coverage["missing_dates"] = missing
    coverage["complete"] = coverage["days_with_ledger"] == coverage["requested_days"]
    coverage["day_coverage_pct"] = round(100.0 * coverage["days_with_ledger"] / max(1, coverage["requested_days"]), 1)
    coverage["expected_monitor_runs_nominal"] = 3 * coverage["requested_days"]
    coverage["monitor_run_coverage_pct"] = round(100.0 * coverage["monitor_runs"] / max(1, coverage["expected_monitor_runs_nominal"]), 1)
    return events, coverage


def preferred_population_metric_from(metrics: dict[str, Any], typ: Any) -> tuple[str | None, float | None]:
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


def event_union(event: dict[str, Any], grade: str | None = None):
    rows = event.get("geometry_records") or []
    geoms = [r.get("geometry") for r in rows if r.get("geometry") is not None and (grade is None or r.get("grade") == grade)]
    return safe_union(geoms)


def event_first_day_union(event: dict[str, Any], grade: str | None = None):
    rows = [r for r in (event.get("geometry_records") or []) if r.get("geometry") is not None and (grade is None or r.get("grade") == grade)]
    if not rows:
        return None
    first_day = min(str(r.get("day") or "") for r in rows)
    return safe_union([r["geometry"] for r in rows if str(r.get("day") or "") == first_day])


def date_of(value: Any) -> date | None:
    dt = parse_dt(value)
    return dt.date() if dt else None


def lifecycle_flags(event: dict[str, Any], start: date, end: date, coverage_complete: bool) -> dict[str, Any]:
    first = date_of(event.get("lifecycle_first_seen")) or (date.fromisoformat(event["days_observed"][0]) if event.get("days_observed") else None)
    last = date_of(event.get("lifecycle_last_seen")) or (date.fromisoformat(event["days_observed"][-1]) if event.get("days_observed") else None)
    new = bool(first and start <= first <= end)
    seen_end = end.isoformat() in (event.get("days_observed") or [])
    resolved = bool(coverage_complete and last and start <= last < end and not seen_end)
    ongoing = not new and not resolved
    if resolved and new:
        label = "New & resolved this period"
    elif resolved:
        label = "Resolved this period"
    elif new:
        label = "New this period"
    else:
        label = "Ongoing"
    return {"label": label, "new_this_period": new, "ongoing": ongoing, "resolved_this_period": resolved,
            "first_seen": event.get("lifecycle_first_seen"), "last_seen": event.get("lifecycle_last_seen")}


def event_summaries(events: dict[str, Any], start: date, end: date, coverage_complete: bool) -> list[dict[str, Any]]:
    rows = []
    for stable, event in events.items():
        peak_key, peak_pop = preferred_population_metric_from(event.get("raw_metrics_max") or {}, event.get("type"))
        latest_key, latest_pop = preferred_population_metric_from(event.get("raw_metrics_latest") or {}, event.get("type"))
        metrics = event.get("raw_metrics_max") or {}
        union = event_union(event)
        first_union = event_first_day_union(event)
        area = geodesic_area_km2(union)
        first_area = geodesic_area_km2(first_union)
        added = None if area is None or first_area is None else max(0.0, area - first_area)
        life = lifecycle_flags(event, start, end, coverage_complete)
        rows.append({
            "stable_id": stable, "type": event.get("type"), "title": event.get("title"), "region": event.get("region"),
            "public_significant": bool(event.get("public_significant")), "max_alert_level": event.get("max_alert_level"),
            "days_observed": len(event.get("days_observed") or []), "lifecycle": life,
            "preferred_population_metric_peak": peak_key, "preferred_population_peak_raw": int(peak_pop) if peak_pop is not None else None,
            "preferred_population_metric_latest": latest_key, "preferred_population_latest_raw": int(latest_pop) if latest_pop is not None else None,
            "period_mapped_footprint_union_km2": round(area, 1) if area is not None else None,
            "additional_mapped_area_after_first_observed_day_km2": round(added, 1) if added is not None else None,
            "burned_area_ha_max": metrics.get("burned_area_ha"),
            "agricultural_drought_impact_area_km2_max": metrics.get("agricultural_drought_impact_area_km2"),
        })
    rows.sort(key=lambda r: (ALERT_RANK.get(str(r.get("max_alert_level") or ""), 0), r.get("preferred_population_peak_raw") or 0), reverse=True)
    return rows


def compute_metrics(events: dict[str, Any], start: date, end: date, coverage_complete: bool) -> dict[str, Any]:
    by_grade_type: dict[tuple[str, str], list[Any]] = {}
    mapped_counts = Counter()
    semantics_counts = Counter()
    for event in events.values():
        seen_pairs = set()
        for rec in event.get("geometry_records") or []:
            g = rec.get("geometry")
            grade = str(rec.get("grade") or "context_grade")
            typ = str(event.get("type") or "Unknown")
            if g is None:
                continue
            by_grade_type.setdefault((grade, typ), []).append(g)
            semantics_counts[f"{grade}:{typ}"] += 1
            seen_pairs.add((grade, typ))
        for _, typ in seen_pairs:
            mapped_counts[typ] += 1

    exposure_types = sorted({typ for (grade, typ) in by_grade_type if grade == "exposure_grade"})
    exposure_by_hazard: dict[str, int] = {}
    exposure_combined = None
    exposure_geoms = [g for (grade, _), gs in by_grade_type.items() if grade == "exposure_grade" for g in gs]
    flood_context_geoms = by_grade_type.get(("context_grade", "Flood"), [])
    pop_path = h.ensure_population_source() if exposure_geoms or flood_context_geoms else None
    flood_context_population = None
    if pop_path:
        with rasterio.open(pop_path) as src:
            combined = safe_union(exposure_geoms)
            if combined is not None:
                exposure_combined = int(h.raster_population(src, combined))
            for typ in exposure_types:
                union = safe_union(by_grade_type.get(("exposure_grade", typ), []))
                if union is not None:
                    exposure_by_hazard[typ] = int(h.raster_population(src, union))
            flood_union = safe_union(flood_context_geoms)
            if flood_union is not None:
                flood_context_population = int(h.raster_population(src, flood_union))

    drought_union = safe_union(by_grade_type.get(("risk_grade", "Drought"), []))
    crop_km2 = None
    if drought_union is not None and CROP_TIF.exists():
        crop_ha = raster_area_ha(CROP_TIF, drought_union, 1)
        crop_km2 = None if crop_ha is None else round(float(crop_ha) / 100.0, 1)

    wildfire_union = safe_union(by_grade_type.get(("exposure_grade", "Wildfire"), []))
    wildfire_unique_km2 = geodesic_area_km2(wildfire_union)
    wildfire_source_ha = 0.0
    wildfire_source_n = 0
    source_pop_sum = 0.0
    source_pop_n = 0
    lifecycle_counts = Counter()
    for event in events.values():
        metrics = event.get("raw_metrics_max") or {}
        if event.get("type") == "Wildfire":
            n = finite(metrics.get("burned_area_ha"))
            if n is not None:
                wildfire_source_ha += n
                wildfire_source_n += 1
        _, pop = preferred_population_metric_from(metrics, event.get("type"))
        if pop is not None:
            source_pop_sum += pop
            source_pop_n += 1
        flags = lifecycle_flags(event, start, end, coverage_complete)
        lifecycle_counts[flags["label"]] += 1

    alert_counts = Counter(str(e.get("max_alert_level") or "Unclassified") for e in events.values())
    type_counts = Counter(str(e.get("type") or "Unknown") for e in events.values())
    return {
        "unique_mapped_population_exposed": {
            "raw_value": exposure_combined, "by_hazard_raw": exposure_by_hazard, "included_hazards": exposure_types,
            "geometry_grade": "exposure_grade",
            "method": "GHSL 2025 population inside the spatial union of public/significant exposure-grade footprints only; overlapping mapped footprints are counted once",
            "interpretation": "Cross-hazard headline excludes context-grade flood event areas and risk-grade drought areas; it is mapped exposure, not verified unique affected individuals.",
        },
        "flood_reported_event_area_population_context": {
            "raw_value": flood_context_population, "geometry_grade": "context_grade",
            "method": "GHSL 2025 population inside the union of public/significant GDACS reported flood event/affected-area polygons",
            "interpretation": "Context only: these polygons are not observed inundation extent and are excluded from the cross-hazard exposure headline.",
        },
        "event_deduped_source_population_sum": {
            "raw_value": int(round(source_pop_sum)) if source_pop_n else None, "contributing_events": source_pop_n,
            "method": "maximum preferred source/model population metric per public/significant stable event, then event-level sum",
            "interpretation": "Stable events are counted once but people shared by different events may still be counted more than once; secondary diagnostic only.",
        },
        "drought_crop_area_within_mapped_risk_footprints_km2": {
            "raw_value": crop_km2, "geometry_grade": "risk_grade",
            "method": "FAO CROPGRIDS 2020 physical crop area inside the spatial union of public/significant mapped drought risk/impact footprints",
            "interpretation": "Spatial crop exposure/context, not confirmed crop damage or crop loss.",
        },
        "wildfire_burned_area": {
            "unique_mapped_union_km2": round(wildfire_unique_km2, 1) if wildfire_unique_km2 is not None else None,
            "unique_mapped_union_ha": round(wildfire_unique_km2 * 100) if wildfire_unique_km2 is not None else None,
            "source_event_deduped_ha": round(wildfire_source_ha) if wildfire_source_n else None,
            "source_contributing_events": wildfire_source_n,
            "headline": "unique_mapped_union_ha",
        },
        "event_counts": {"total_unique_events": len(events), "by_hazard": dict(sorted(type_counts.items())), "by_max_alert": dict(sorted(alert_counts.items()))},
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
        "mapped_event_counts_by_hazard": dict(sorted(mapped_counts.items())),
        "geometry_semantics_counts": dict(sorted(semantics_counts.items())),
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
    exposure = (m.get("unique_mapped_population_exposed") or {}).get("raw_value")
    flood_context = (m.get("flood_reported_event_area_population_context") or {}).get("raw_value")
    crop = (m.get("drought_crop_area_within_mapped_risk_footprints_km2") or {}).get("raw_value")
    fires = m.get("wildfire_burned_area") or {}
    counts = m.get("event_counts") or {}
    all_counts = report.get("all_monitored_event_counts") or {}
    coverage = report.get("data_coverage") or {}
    complete = bool(coverage.get("complete"))
    status = report.get("publication_status") or ("Frozen publication" if report.get("frozen") else "Rolling preview")
    badge = '<div class="warning"><strong>Partial coverage.</strong> Headline values are provisional because not every requested UTC day has a daily ledger.</div>' if not complete else '<div class="ok"><strong>Complete day coverage.</strong> Every requested UTC day has a reporting ledger.</div>'
    crop_text = "—" if crop is None else f"{float(crop):,.1f} km²"
    fire_text = "—" if fires.get("unique_mapped_union_ha") is None else f"{int(fires['unique_mapped_union_ha']):,} ha"
    by_hazard = "".join(f"<li><strong>{html_escape(k)}</strong>: {html_escape(v)}</li>" for k, v in (counts.get("by_hazard") or {}).items()) or "<li>No significant events in available ledger.</li>"
    life = "".join(f"<li><strong>{html_escape(k)}</strong>: {html_escape(v)}</li>" for k, v in (m.get("lifecycle_counts") or {}).items()) or "<li>No lifecycle records.</li>"
    top = "".join(
        f"<tr><td>{html_escape(e.get('type'))}</td><td>{html_escape(e.get('title'))}</td><td>{html_escape(e.get('region'))}</td><td>{html_escape((e.get('lifecycle') or {}).get('label') or '—')}</td><td>{html_escape(e.get('max_alert_level') or '—')}</td><td>{html_escape(display_people(e.get('preferred_population_peak_raw')))}</td><td>{html_escape(display_people(e.get('preferred_population_latest_raw')))}</td></tr>"
        for e in (report.get("events") or [])[:25]
    ) or '<tr><td colspan="7">No public/significant events in the available reporting ledger.</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(report['label'])} · Climate Pulse</title>
<style>body{{font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;background:#f4f8fa;color:#203746}}main{{max-width:1100px;margin:auto;padding:24px}}a{{color:#17658e}}.hero,.card{{background:#fff;border:1px solid #dce7ed;border-radius:18px;padding:20px;margin-bottom:14px}}.hero h1{{margin:0 0 6px}}.muted{{color:#6c8190}}.grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}}.metric{{border:1px solid #e1e9ee;border-radius:14px;padding:14px}}.metric b{{display:block;font-size:12px;text-transform:uppercase;color:#6b8190;margin-bottom:6px}}.metric span{{font-size:21px;font-weight:800}}.warning,.ok{{padding:11px 13px;border-radius:12px;margin-top:12px}}.warning{{background:#fff4e5;border:1px solid #edc483;color:#744b09}}.ok{{background:#eef8f2;border:1px solid #b8ddc5;color:#285d3a}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #e6edf1;text-align:left;font-size:12px;vertical-align:top}}@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}main{{padding:12px}}.metric span{{font-size:18px}}table{{display:block;overflow:auto}}}}</style></head><body><main>
<div class="hero"><a href="../../reports.html">← Reports</a><h1>{html_escape(report['label'])}</h1><div class="muted">{report['period']['start']} to {report['period']['end']} UTC · {html_escape(status)} · generated {html_escape(report['generated_at'])}</div>{badge}</div>
<div class="grid"><div class="metric"><b>Unique mapped population exposed · exposure-grade</b><span>{html_escape(display_people(exposure))}</span></div><div class="metric"><b>Flood reported-area population · context only</b><span>{html_escape(display_people(flood_context))}</span></div><div class="metric"><b>Drought crop area · risk footprints</b><span>{html_escape(crop_text)}</span></div><div class="metric"><b>Unique mapped wildfire burned area</b><span>{html_escape(fire_text)}</span></div><div class="metric"><b>Public/significant events</b><span>{html_escape(counts.get('total_unique_events',0))}</span></div></div>
<div class="card"><h2>Coverage & event universes</h2><p>{coverage.get('days_with_ledger',0)} of {coverage.get('requested_days',0)} requested days have a ledger ({coverage.get('day_coverage_pct',0)}%); {coverage.get('monitor_runs',0)} monitor runs are represented. Nominal schedule would provide {coverage.get('expected_monitor_runs_nominal',0)} runs.</p><p><strong>Headline universe:</strong> events that met the public/significant display rule at least once in the period. <strong>All monitored universe:</strong> {html_escape(all_counts.get('total_unique_events',0))} stable events retained for audit.</p><ul>{by_hazard}</ul></div>
<div class="card"><h2>Lifecycle</h2><ul>{life}</ul><p class="muted">Resolved status is only asserted when period day coverage is complete and the event is no longer observed by the period end; otherwise the report fails conservatively toward ongoing/unknown.</p></div>
<div class="card"><h2>Significant-event summary</h2><table><thead><tr><th>Hazard</th><th>Event</th><th>Region</th><th>Lifecycle</th><th>Max alert</th><th>Peak source/model population</th><th>Latest source/model population</th></tr></thead><tbody>{top}</tbody></table></div>
<div class="card"><h2>Interpretation</h2><p><strong>Cross-hazard population headline:</strong> only exposure-grade mapped wildfire/cyclone footprints are spatially unioned before GHSL extraction. Context-grade GDACS flood event areas are reported separately because they are not observed inundation extent. Risk-grade drought polygons are used for crop/risk context.</p><p><strong>Wildfire burned area:</strong> the headline is the spatial union of mapped wildfire footprints, so overlap is removed. The event-deduped sum of source burned-area values remains in JSON as a secondary diagnostic.</p><p><strong>Drought crop area:</strong> FAO CROPGRIDS physical crop area inside mapped drought risk/impact footprints; not confirmed crop loss.</p><p class="muted">Exact raw values are retained in companion JSON; displayed population counts are rounded to the nearest thousand, with values below 1,000 shown as &lt;1,000.</p></div>
</main></body></html>"""


def update_index(report: dict[str, Any], html_rel: str, json_rel: str) -> None:
    REPORT_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    idx = load_json(INDEX_PATH, {"schema_version": "2.0", "updated_at": None, "rolling": {}, "weekly": [], "monthly": []})
    idx["schema_version"] = "2.0"
    item = {
        "id": report["id"], "label": report["label"], "start": report["period"]["start"], "end": report["period"]["end"],
        "generated_at": report["generated_at"], "html": html_rel, "json": json_rel,
        "coverage_complete": bool((report.get("data_coverage") or {}).get("complete")),
        "coverage_days": (report.get("data_coverage") or {}).get("days_with_ledger"),
        "requested_days": (report.get("data_coverage") or {}).get("requested_days"),
        "publication_status": report.get("publication_status"),
    }
    if report["kind"] == "rolling":
        idx.setdefault("rolling", {})[report["id"]] = item
    else:
        rows = [x for x in (idx.get(report["kind"]) or []) if x.get("id") != report["id"]]
        rows.append(item)
        rows.sort(key=lambda x: x.get("id", ""), reverse=True)
        idx[report["kind"]] = rows
    idx["updated_at"] = report["generated_at"]
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def counts_for(events: dict[str, Any]) -> dict[str, Any]:
    return {"total_unique_events": len(events), "by_hazard": dict(sorted(Counter(str(e.get('type') or 'Unknown') for e in events.values()).items()))}


def publication_status_for(frozen: bool, coverage_complete: bool) -> str:
    if frozen and not coverage_complete:
        return "withheld_incomplete_coverage"
    if frozen:
        return "complete_frozen_publication"
    return "complete_preview" if coverage_complete else "partial_preview"


def write_report(spec: dict[str, Any]) -> dict[str, Any]:
    all_events, coverage = load_period(spec["start"], spec["end"])
    significant = {k: v for k, v in all_events.items() if v.get("public_significant")}
    metrics = compute_metrics(significant, spec["start"], spec["end"], bool(coverage["complete"]))
    generated = iso()
    frozen = bool(spec["frozen"])
    withheld = frozen and not coverage["complete"]
    publication_status = publication_status_for(frozen, bool(coverage["complete"]))
    report = {
        "schema_version": "2.0", "kind": spec["kind"], "id": spec["id"], "label": spec["label"], "frozen": frozen,
        "publication_status": publication_status, "generated_at": generated,
        "period": {"start": spec["start"].isoformat(), "end": spec["end"].isoformat(), "timezone": "UTC"},
        "data_coverage": coverage, "metrics": metrics,
        "event_universe": {"headline": "public_significant", "all_monitored_count": len(all_events), "public_significant_count": len(significant)},
        "all_monitored_event_counts": counts_for(all_events),
        "events": event_summaries(significant, spec["start"], spec["end"], bool(coverage["complete"])),
        "all_monitored_events": event_summaries(all_events, spec["start"], spec["end"], bool(coverage["complete"])),
        "authority": {"raw": "data/events/archive (3x-daily enriched snapshots)", "reporting_ledger": "data/history/daily", "population": "JRC GHSL GHS-WUP-POP R2025A epoch 2025", "crops": "FAO CROPGRIDS v1.08 2020"},
        "deduplication": {"event": "stable source identity, preferring GDACS ID when available", "spatial": "union same-grade reporting footprints before raster extraction", "geometry_semantics": "exposure-grade headline; flood context-grade separate; drought risk-grade separate"},
    }
    if withheld:
        data_dir = REPORT_DATA_ROOT / "withheld" / spec["kind"]
        html_dir = REPORT_HTML_ROOT / "withheld" / spec["kind"]
    else:
        data_dir = REPORT_DATA_ROOT / spec["kind"]
        html_dir = REPORT_HTML_ROOT / spec["kind"]
    data_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / f"{spec['id']}.json"
    html_path = html_dir / f"{spec['id']}.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    if not withheld:
        update_index(report, str(html_path.relative_to(ROOT)), str(json_path.relative_to(ROOT)))
    return {
        "kind": spec["kind"], "id": spec["id"], "json": str(json_path.relative_to(ROOT)), "html": str(html_path.relative_to(ROOT)),
        "all_events": len(all_events), "significant_events": len(significant), "coverage_days": coverage["days_with_ledger"],
        "coverage_complete": coverage["complete"], "publication_status": publication_status,
    }


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
