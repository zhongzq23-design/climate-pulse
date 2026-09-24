#!/usr/bin/env python3
"""Source-first compatibility runner for Climate Pulse hazard enrichment.

Prefer structured GDACS/GWIS modelled exposure fields when they exist. Use
Climate Pulse polygon × GHSL calculations only when the equivalent structured
source metric is unavailable, preserve metric provenance, and keep exposure
separate from observed harm.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from copy import deepcopy
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import unary_union

import enrich_hazard_exposure as h
from source_metric_utils import (
    drought_agricultural_impact_area, episode_details_url, gdacs_alert_level,
    impact_resources, latest_episode_id, parse_cyclone_timeline,
    parse_wildfire_impact,
)

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
GDACS_EPISODE = "https://www.gdacs.org/gdacsapi/api/events/getepisodedata"
GREEN_WILDFIRE_MIN_HA = 10_000.0
GREEN_WILDFIRE_MIN_POP_5KM = 10_000


def polygon_parts(g):
    if g is None or g.is_empty: return []
    if isinstance(g, Polygon): return [g]
    if isinstance(g, MultiPolygon): return list(g.geoms)
    if isinstance(g, GeometryCollection):
        out = []
        for x in g.geoms: out.extend(polygon_parts(x))
        return out
    return []


def safe_polygons_union(features, predicate=None):
    geoms = []
    for f in features:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}: continue
        if predicate is not None and not predicate(f): continue
        try:
            g = shape(geom)
            if not g.is_valid: g = make_valid(g)
            for p in polygon_parts(g):
                if not p.is_valid: p = p.buffer(0)
                if not p.is_empty: geoms.append(p)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN skipping invalid GDACS polygon: {type(exc).__name__}: {exc}")
    if not geoms: return None
    try:
        out = unary_union(geoms)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN unary_union failed; retrying repaired parts: {type(exc).__name__}: {exc}")
        repaired = []
        for g in geoms:
            try: repaired.extend(polygon_parts(make_valid(g)))
            except Exception: pass  # noqa: BLE001
        if not repaired: return None
        out = unary_union(repaired)
    if not out.is_valid: out = make_valid(out)
    parts = polygon_parts(out)
    return unary_union(parts) if parts else None


def promote_gdacs_alert(event):
    level = gdacs_alert_level(event)
    if level == "Red": event["priority"] = "High"
    elif level == "Orange" and event.get("priority") != "High": event["priority"] = "Medium"
    if level: event["alert_level"] = level
    return event


def gdacs_url(url: str | None) -> bool:
    if not url: return False
    try: host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError: return False
    return host == "gdacs.org" or host.endswith(".gdacs.org")


def episode_payload(event_type: str, event_id: str) -> tuple[Any | None, int | None]:
    try: event_doc = h.gdacs_detail(event_type, event_id)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN source-first event detail {event_type} {event_id}: {type(exc).__name__}: {exc}")
        return None, None
    episode = latest_episode_id(event_doc)
    details = episode_details_url(event_doc, episode)
    if gdacs_url(details):
        try: return h.fetch_json(details, timeout=45), episode
        except Exception as exc:  # noqa: BLE001
            print(f"WARN source-first episode URL {event_type} {event_id}: {type(exc).__name__}: {exc}")
    if episode is not None:
        for source in ("OverAll", "GDACS", ""):
            params = {"eventtype": event_type, "eventid": event_id, "episodeid": episode}
            if source: params["source"] = source
            try: return h.fetch_json(GDACS_EPISODE + "?" + urllib.parse.urlencode(params), timeout=45), episode
            except Exception: pass  # noqa: BLE001
    return None, episode


def first_source_resource(event_type: str, event_id: str, key: str):
    episode_doc, episode = episode_payload(event_type, event_id)
    if episode_doc is None: return None, None, episode
    for source, url in impact_resources(episode_doc, key):
        if not gdacs_url(url): continue
        try: return h.fetch_json(url, timeout=45), source, episode
        except Exception as exc:  # noqa: BLE001
            print(f"WARN source-first {key} {event_type} {event_id}: {type(exc).__name__}: {exc}")
    return None, None, episode


def metric_meta(source: str, field: str, method: str = "source_modelled") -> dict[str, Any]:
    return {"source": source, "source_field": field, "method": method,
            "derived_by_climate_pulse": method != "source_modelled"}


def fallback_meta(field: str, method: str) -> dict[str, Any]:
    return {"source": "Climate Pulse", "source_field": field, "method": method,
            "derived_by_climate_pulse": True}


def _wildfire_source_metrics(event: dict[str, Any]) -> dict[str, Any]:
    for eid in h.gdacs_member_ids(event, "WF"):
        payload, source, episode = first_source_resource("WF", eid, "impact")
        if payload is None: continue
        values = parse_wildfire_impact(payload)
        if not any(k.startswith("population_") for k in values): continue
        values.update({"gdacs_event_id": eid, "gdacs_episode_id": episode,
                       "source": f"GDACS/{source or 'GWIS'}"})
        return values
    return {}


def _tc_source_metrics(event: dict[str, Any]) -> dict[str, Any]:
    for eid in h.gdacs_member_ids(event, "TC"):
        payload, source, episode = first_source_resource("TC", eid, "timeline")
        if payload is None: continue
        values = parse_cyclone_timeline(payload)
        if not any(k.startswith("population_") for k in values): continue
        values.update({"gdacs_event_id": eid, "gdacs_episode_id": episode,
                       "source": f"GDACS/{source or 'advisory'}"})
        return values
    return {}


h.polygons_union = safe_polygons_union
_original_enrich_storm = h.enrich_storm


def source_first_enrich_wildfire(event, src):
    event = promote_gdacs_alert(dict(event)); native = _wildfire_source_metrics(event)
    out = deepcopy(event)
    x = deepcopy(out.get("exposure")) if isinstance(out.get("exposure"), dict) else {"hazard": "wildfire"}
    provenance: dict[str, Any] = {}
    if native:
        src_name = native.get("source") or "GDACS/GWIS"
        for src_field, dst_field, gdacs_field in (
            ("population_burned_area", "population_direct", "POPAFFECTED"),
            ("population_within_1km", "population_within_1km", "SUMPOP1.0"),
            ("population_within_2km", "population_within_2km", "SUMPOP2.0"),
            ("population_within_5km", "population_within_5km", "SUMPOP5.0"),
            ("population_within_10km", "population_within_10km", "SUMPOP10.0"),
        ):
            if native.get(src_field) is not None:
                x[dst_field] = native[src_field]; provenance[dst_field] = metric_meta(src_name, gdacs_field)
        x.update({"source_model": "GDACS/GWIS wildfire exposure", "source_model_run": native.get("model_run"),
                  "source_model_status": native.get("model_status"), "gdacs_event_id": native.get("gdacs_event_id"),
                  "gdacs_episode_id": native.get("gdacs_episode_id"), "quality": "source_modelled_exposure"})
    missing_direct = x.get("population_direct") is None
    missing_5km = x.get("population_within_5km") is None
    if missing_direct or missing_5km:
        ids = h.gdacs_member_ids(event, "WF")
        if ids:
            eid = str(native.get("gdacs_event_id") if native else ids[0])
            episode = native.get("gdacs_episode_id") if native else None
            if episode is None: episode = h.latest_episode("WF", eid)
            geom = h.polygons_union(h.polygon_features("WF", eid, episode))
            if geom is not None:
                x.setdefault("gdacs_event_id", eid); x.setdefault("gdacs_episode_id", episode)
                x.setdefault("footprint_method", "GDACS burned-area/event polygon")
                if missing_direct:
                    x["population_direct"] = h.raster_population(src, geom)
                    provenance["population_direct"] = fallback_meta("population_direct", "GDACS mapped fire polygon × JRC GHSL 2025")
                if missing_5km:
                    x["population_within_5km"] = h.raster_population(src, geom, buffer_m=5000.0)
                    provenance["population_within_5km"] = fallback_meta("population_within_5km", "GDACS mapped fire polygon + 5 km × JRC GHSL 2025")
                if not native: x["quality"] = "high_spatial_reference_fallback"
            else: out["exposure_status"] = "wildfire_polygon_unavailable"
    x.pop("population_5km_ring", None)
    x["metric_provenance"] = provenance
    x["interpretation"] = "Population exposure is modelled spatial exposure, not observed harm, casualties or evacuation."
    out["exposure"] = x; out["alert_level"] = gdacs_alert_level(out) or out.get("alert_level")
    if out.get("priority") in {"Medium", "High"}:
        eligible, rule = True, "gdacs_orange_red"
    else:
        try: eligible = float(out.get("burned_area_ha")) >= GREEN_WILDFIRE_MIN_HA and int(x.get("population_within_5km")) > GREEN_WILDFIRE_MIN_POP_5KM
        except (TypeError, ValueError): eligible = False
        rule = "green_requires_10000ha_and_population_within_5km_gt_10000"
    out["display_eligible"] = eligible; out["display_rule"] = rule
    h.write_exposure(out, x)
    return out, eligible


def source_first_enrich_storm(event, src):
    event = promote_gdacs_alert(dict(event)); native = _tc_source_metrics(event)
    if not native:
        out, eligible = _original_enrich_storm(event, src); out = promote_gdacs_alert(out)
        x = out.get("exposure") if isinstance(out.get("exposure"), dict) else {}
        prov = deepcopy(x.get("metric_provenance")) if isinstance(x.get("metric_provenance"), dict) else {}
        if x.get("population_ts_or_impact_footprint") is not None:
            prov["population_ts_or_impact_footprint"] = fallback_meta("population_ts_or_impact_footprint", "GDACS TC polygon × JRC GHSL 2025")
        if x.get("population_within_300km_of_center") is not None:
            prov["population_within_300km_of_center"] = fallback_meta("population_within_300km_of_center", "300 km centre proximity × JRC GHSL 2025")
        if prov: x["metric_provenance"] = prov; out["exposure"] = x
        if out.get("priority") in {"Medium", "High"}: eligible = True; out["display_eligible"] = True
        return out, eligible
    out = deepcopy(event); source = native.get("source") or "GDACS/advisory"
    x = deepcopy(out.get("exposure")) if isinstance(out.get("exposure"), dict) else {"hazard": "tropical_cyclone"}
    provenance: dict[str, Any] = {}
    for src_field, dst_field, gdacs_field in (
        ("population_wind_39kt", "population_wind_39kt", "pop39"),
        ("population_wind_74kt", "population_wind_74kt", "pop74"),
        ("population_storm_surge", "population_storm_surge", "popstormsurge"),
    ):
        if native.get(src_field) is not None:
            x[dst_field] = native[src_field]; provenance[dst_field] = metric_meta(source, gdacs_field)
    x.update({"gdacs_event_id": native.get("gdacs_event_id"), "gdacs_episode_id": native.get("gdacs_episode_id"),
              "advisory_id": native.get("id"), "advisory_number": native.get("advisory_number"),
              "advisory_datetime": native.get("advisory_datetime"), "advisory_actual": native.get("actual"),
              "advisory_current": native.get("current"), "quality": "source_modelled_exposure",
              "metric_provenance": provenance,
              "interpretation": "GDACS advisory exposure is modelled population within defined hazard zones; it is not observed harm."})
    out["exposure"] = x; out["alert_level"] = gdacs_alert_level(out) or out.get("alert_level")
    exposed = max(int(x.get(k) or 0) for k in ("population_wind_39kt", "population_wind_74kt", "population_storm_surge"))
    eligible = out.get("priority") in {"Medium", "High"} or exposed > 0
    out["display_eligible"] = eligible; out["display_rule"] = "gdacs_orange_red_or_nonzero_source_modelled_exposure"
    h.write_exposure(out, x)
    return out, eligible


h.enrich_wildfire = source_first_enrich_wildfire
h.enrich_storm = source_first_enrich_storm
cache_root = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse")).expanduser()
h.CACHE_ROOT = cache_root; h.CACHE_POP = cache_root / "population" / "ghsl_wup_2025_1km.tif"


def _patch_source_semantics(event: dict[str, Any]) -> dict[str, Any]:
    event = promote_gdacs_alert(event)
    if event.get("type") != "Drought": return event
    area = drought_agricultural_impact_area(event.get("summary"))
    if area is None: return event
    source_metrics = deepcopy(event.get("source_metrics")) if isinstance(event.get("source_metrics"), dict) else {}
    source_metrics["agricultural_drought_impact_area_km2"] = area
    source_metrics["agricultural_drought_impact_area_label"] = "Agricultural drought potential-impact/risk area"
    source_metrics["metric_provenance"] = {"agricultural_drought_impact_area_km2": {
        "source": "GDACS / Global Drought Observatory", "method": "source_published_event_severity_text",
        "derived_by_climate_pulse": False,
        "interpretation": "Potential agricultural drought impact/risk area; not confirmed crop-loss area."}}
    event["source_metrics"] = source_metrics
    return event


def patch_snapshot_metadata() -> None:
    if not LATEST.exists(): return
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    canonical = [_patch_source_semantics(deepcopy(e)) for e in (snap.get("canonical_events") or [])]
    by_id = {str(e.get("id")): e for e in canonical}; display = []
    for original in snap.get("events") or []:
        event = deepcopy(original); key = str(event.get("id"))
        if key in by_id:
            event["alert_level"] = by_id[key].get("alert_level")
            if by_id[key].get("source_metrics"): event["source_metrics"] = deepcopy(by_id[key]["source_metrics"])
        if event.get("type") == "Wildfire" and isinstance(event.get("members"), list):
            event["members"] = [_patch_source_semantics(deepcopy(by_id.get(str(m.get("id")), m))) for m in event["members"]]
        display.append(_patch_source_semantics(event))
    snap["canonical_events"] = canonical; snap["events"] = display; snap["schema_version"] = "1.5"
    hz = snap.setdefault("hazard_exposure", {}); hz.pop("population_dataset", None)
    hz.update({
        "fallback_population_dataset": "JRC GHSL GHS-WUP-POP R2025A, epoch 2025, used only for Climate Pulse population fallback calculations",
        "authority_policy": "source-first: GDACS/GWIS structured modelled exposure first; Climate Pulse-derived metrics only as fallback or clearly labelled supplemental context",
        "wildfire_rule": "GDACS Orange/Red visible; Green requires burned area >= 10,000 ha AND population within 5 km > 10,000. GDACS/GWIS POP scalars are preferred; polygon × GHSL is fallback only.",
        "storm_rule": "GDACS advisory pop39/pop74/popstormsurge preferred; polygon × GHSL is fallback when structured source exposure is unavailable. Orange/Red remain visible.",
        "drought_rule": "Use GDACS/GDO agricultural drought risk/impact information when published. Do not infer actual crop loss or affected population; derived land/forest/crop overlap is supplemental spatial context.",
        "terminology": {"exposed_population": "people spatially inside a modelled hazard footprint or buffer",
                        "affected_population": "use only when a source reports actual impacts",
                        "estimated_exposed_population": "Climate Pulse-derived spatial estimate when the upstream equivalent is unavailable"},
        "interpretation": "Exposure is not observed harm, casualties, displacement, crop loss or economic loss.",
    })
    monitor = snap.setdefault("monitor", {}); monitor["metric_authority_policy"] = "source_first"
    monitor["wildfire_green_display_rule"] = "burned_area_ha >= 10000 AND population_within_5km > 10000"
    monitor["human_impact_progressive_disclosure"] = "highlight actual human-impact variables primarily for Red/severe events and only when a reliable source provides them"
    monitor.pop("drought_exposure_rule", None)
    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)


if __name__ == "__main__":
    h.main()
    patch_snapshot_metadata()
