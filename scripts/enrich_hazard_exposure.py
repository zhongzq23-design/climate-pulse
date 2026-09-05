#!/usr/bin/env python3
"""Enrich Climate Pulse wildfire and tropical-cyclone events with population exposure.

This script runs after ``monitor_events.py``. It keeps the raw/canonical event
archive intact, then adds hazard-specific population screening and rebuilds the
public display list.

Wildfire
--------
* Major-fire gate remains burned area >= 10,000 ha (applied upstream).
* Fetch the latest GDACS wildfire perimeter.
* Estimate population inside the burned-area polygon and inside a 5 km buffer
  with the authoritative JRC GHSL GHS-WUP-POP R2025A 2025 ~1 km grid.

Tropical cyclone / storm
------------------------
* GDACS TC: fetch the latest GDACS event polygons, prefer wind / TS-force
  polygons, and estimate exposed population with GHSL.
* Orange/Red GDACS storms always remain visible; Green storms remain visible
  only if the wind/impact footprint intersects populated cells.
* EONET-only storms use a conservative 300 km populated-proximity fallback.
* CEMS storm activations remain visible because they are already human-triggered
  emergency mapping activations.

Important: these are *population exposure* estimates, not casualties, losses,
or proof that every person in the footprint was affected.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

# Reuse the canonical map clustering and date utilities.
from monitor_events import cluster_fires, parse_dt

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
POP_META = ROOT / "data" / "reference" / "population" / "metadata.json"
EXPOSURE_DIR = ROOT / "data" / "exposure" / "population"
CACHE_ROOT = Path(os.environ.get("CLIMATE_PULSE_CACHE_DIR", Path.home() / ".cache" / "climate-pulse"))
CACHE_POP = CACHE_ROOT / "population" / "ghsl_wup_2025_1km.tif"

GDACS_EVENT = "https://www.gdacs.org/gdacsapi/api/events/geteventdata"
GDACS_POLYGON = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
USER_AGENT = "ClimatePulse/0.3 (+https://zhongzq23-design.github.io/climate-pulse/)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_json(url: str, timeout: int = 45) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def pick_population_tif(folder: Path) -> Path:
    tifs = [p for p in folder.rglob("*.tif") if p.is_file()]
    if not tifs:
        raise RuntimeError("GHSL archive contained no GeoTIFF")
    tifs.sort(key=lambda p: ("POP" not in p.name.upper(), len(p.name)))
    return tifs[0]


def ensure_population_source() -> Path:
    if CACHE_POP.exists() and CACHE_POP.stat().st_size > 1_000_000:
        return CACHE_POP
    meta = json.loads(POP_META.read_text(encoding="utf-8"))
    url = meta.get("authoritative_source_url")
    if not url:
        raise RuntimeError("Population metadata has no authoritative_source_url")
    CACHE_POP.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cp-ghsl-") as td:
        td_path = Path(td)
        z = td_path / "ghsl.zip"
        print(f"Downloading authoritative GHSL 1 km population source: {url}")
        download(url, z)
        extract = td_path / "extract"
        extract.mkdir()
        with zipfile.ZipFile(z) as zf:
            zf.extractall(extract)
        src = pick_population_tif(extract)
        shutil.copy2(src, CACHE_POP)
    return CACHE_POP


def all_episode_ids(obj: Any) -> list[int]:
    found: list[int] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower().replace("_", "")
            if kl in {"episodeid", "episode"}:
                try:
                    found.append(int(v))
                except (TypeError, ValueError):
                    pass
            found.extend(all_episode_ids(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(all_episode_ids(v))
    return found


def gdacs_detail(event_type: str, event_id: str) -> Any:
    return fetch_json(GDACS_EVENT + "?" + urllib.parse.urlencode({"eventtype": event_type, "eventid": event_id}))


def latest_episode(event_type: str, event_id: str) -> int | None:
    try:
        detail = gdacs_detail(event_type, event_id)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN detail {event_type} {event_id}: {type(exc).__name__}: {exc}")
        return None
    ids = all_episode_ids(detail)
    return max(ids) if ids else None


def polygon_features(event_type: str, event_id: str, episode_id: int | None) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    if episode_id is not None:
        attempts += [
            {"eventtype": event_type, "eventid": event_id, "episodeid": episode_id, "source": "OverAll"},
            {"eventtype": event_type, "eventid": event_id, "episodeid": episode_id},
        ]
    attempts.append({"eventtype": event_type, "eventid": event_id})
    last_exc: Exception | None = None
    for params in attempts:
        try:
            data = fetch_json(GDACS_POLYGON + "?" + urllib.parse.urlencode(params))
            feats = data.get("features", []) if isinstance(data, dict) else []
            if feats:
                return [f for f in feats if isinstance(f, dict)]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc:
        print(f"WARN polygons {event_type} {event_id}: {type(last_exc).__name__}: {last_exc}")
    return []


def polygons_union(features: list[dict[str, Any]], predicate=None):
    geoms = []
    for f in features:
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        if predicate is not None and not predicate(f):
            continue
        try:
            g = shape(geom)
            if not g.is_empty:
                geoms.append(g)
        except Exception:  # noqa: BLE001
            continue
    if not geoms:
        return None
    out = unary_union(geoms)
    if not out.is_valid:
        out = out.buffer(0)
    return out if not out.is_empty else None


def tc_feature_text(feature: dict[str, Any]) -> str:
    return json.dumps(feature.get("properties") or {}, ensure_ascii=False).lower()


def tc_wind_union(features: list[dict[str, Any]]):
    """Prefer explicit tropical-storm / wind polygons; return geometry + method."""
    def explicit(f: dict[str, Any]) -> bool:
        t = tc_feature_text(f)
        if any(x in t for x in ("storm surge", "stormsurge", "rainfall", "precip")):
            return False
        return any(x in t for x in ("34kt", "34 kt", "34 knots", "tropical storm", "wind", "63 km/h", "64kt", "64 kt"))

    g = polygons_union(features, explicit)
    if g is not None:
        return g, "gdacs_wind_polygon"
    # GDACS event polygon response is still a better impact-screening fallback
    # than a cyclone-centre point. Mark the lower specificity in metadata.
    return polygons_union(features), "gdacs_event_polygon_fallback"


def raster_population(src: rasterio.io.DatasetReader, geom4326, buffer_m: float = 0.0) -> int:
    if geom4326 is None or geom4326.is_empty:
        return 0
    src_geom = shape(transform_geom("EPSG:4326", src.crs, mapping(geom4326), precision=7))
    if buffer_m:
        src_geom = src_geom.buffer(buffer_m)
    if src_geom.is_empty:
        return 0
    try:
        arr, _ = mask(src, [mapping(src_geom)], crop=True, filled=False, all_touched=False)
    except ValueError:
        return 0
    data = arr[0]
    vals = data.compressed().astype("float64", copy=False) if np.ma.isMaskedArray(data) else data.astype("float64", copy=False).ravel()
    vals = vals[np.isfinite(vals) & (vals >= 0)]
    return max(0, int(round(float(vals.sum(dtype="float64")))))


def point_buffer_population(src: rasterio.io.DatasetReader, lon: float, lat: float, radius_m: float) -> int:
    p = Point(float(lon), float(lat))
    return raster_population(src, p, buffer_m=radius_m)


def gdacs_member_ids(event: dict[str, Any], event_type: str) -> list[str]:
    ids = []
    if event.get("origin") == "gdacs":
        ids.append(str(event.get("source_id")))
    for m in event.get("source_members") or []:
        if isinstance(m, dict) and m.get("origin") == "gdacs" and m.get("source_id") is not None:
            ids.append(str(m["source_id"]))
    # Cluster members are canonical events and may hold their own source_members.
    for member in event.get("members") or []:
        if isinstance(member, dict):
            ids.extend(gdacs_member_ids(member, event_type))
    return list(dict.fromkeys(x for x in ids if x and x != "None"))


def write_exposure(event: dict[str, Any], payload: dict[str, Any]) -> None:
    EXPOSURE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(event.get("id") or "event"))[:160]
    path = EXPOSURE_DIR / f"{safe}.json"
    doc = {
        "schema_version": "1.0",
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "region": event.get("region"),
        "calculated_at": now_iso(),
        "population_reference": {
            "dataset": "JRC GHSL GHS-WUP-POP R2025A",
            "epoch": 2025,
            "resolution": "~1 km authoritative source",
            "interpretation": "Population exposure, not observed casualties or economic loss",
        },
        **payload,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enrich_wildfire(event: dict[str, Any], src) -> tuple[dict[str, Any], bool]:
    out = deepcopy(event)
    ids = gdacs_member_ids(event, "WF")
    if not ids:
        return out, True
    # Canonical wildfire records are source-deduplicated; use the first GDACS id.
    eid = ids[0]
    episode = latest_episode("WF", eid)
    feats = polygon_features("WF", eid, episode)
    geom = polygons_union(feats)
    if geom is None:
        out["exposure_status"] = "wildfire_polygon_unavailable"
        return out, True
    direct = raster_population(src, geom)
    within5 = raster_population(src, geom, buffer_m=5000.0)
    exposure = {
        "hazard": "wildfire",
        "gdacs_event_id": eid,
        "gdacs_episode_id": episode,
        "footprint_method": "GDACS burned-area/event polygon",
        "population_direct": direct,
        "population_within_5km": within5,
        "population_5km_ring": max(0, within5 - direct),
        "quality": "high_spatial_reference",
    }
    out["exposure"] = exposure
    write_exposure(out, exposure)
    return out, True


def enrich_storm(event: dict[str, Any], src) -> tuple[dict[str, Any], bool]:
    out = deepcopy(event)
    gdacs_ids = gdacs_member_ids(event, "TC")
    members = event.get("source_members") or []
    has_cems = event.get("origin") == "cems" or any(isinstance(m, dict) and m.get("origin") == "cems" for m in members)

    if gdacs_ids:
        eid = gdacs_ids[0]
        episode = latest_episode("TC", eid)
        feats = polygon_features("TC", eid, episode)
        geom, method = tc_wind_union(feats)
        if geom is not None:
            pop = raster_population(src, geom)
            exposure = {
                "hazard": "tropical_cyclone",
                "gdacs_event_id": eid,
                "gdacs_episode_id": episode,
                "footprint_method": method,
                "population_ts_or_impact_footprint": pop,
                "quality": "high" if method == "gdacs_wind_polygon" else "screening",
            }
            out["exposure"] = exposure
            # Orange/Red always shown. Green requires some populated exposure.
            eligible = out.get("priority") in {"Medium", "High"} or pop > 0
            out["display_rule"] = "gdacs_orange_red_or_populated_tc_footprint"
            out["display_eligible"] = eligible
            write_exposure(out, exposure)
            return out, eligible
        # A GDACS cyclone without a usable footprint falls through to proximity.

    if has_cems:
        out["display_rule"] = "cems_activation"
        out["display_eligible"] = True
        return out, True

    # Fallback for EONET-only storms: use a 300 km populated-proximity screen.
    # This avoids showing remote-ocean points while keeping storms close enough
    # to inhabited land to be potentially relevant. It is explicitly not a
    # wind-impact footprint.
    pop300 = point_buffer_population(src, out["lon"], out["lat"], 300_000.0)
    exposure = {
        "hazard": "storm",
        "footprint_method": "300_km_point_population_fallback",
        "population_within_300km_of_center": pop300,
        "quality": "screening",
    }
    out["exposure"] = exposure
    out["display_rule"] = "eonet_center_within_300km_of_populated_land"
    out["display_eligible"] = pop300 > 0
    write_exposure(out, exposure)
    return out, pop300 > 0


def enrich_cluster_exposure(event: dict[str, Any]) -> None:
    if event.get("type") != "Wildfire" or not isinstance(event.get("members"), list):
        return
    exps = [m.get("exposure") for m in event["members"] if isinstance(m, dict) and isinstance(m.get("exposure"), dict)]
    if not exps:
        return
    event["exposure"] = {
        "hazard": "wildfire_cluster",
        "population_direct_member_sum": sum(int(x.get("population_direct") or 0) for x in exps),
        "population_within_5km_member_sum": sum(int(x.get("population_within_5km") or 0) for x in exps),
        "aggregation_note": "Member sum; nearby fire buffers may overlap and are not unique-population counts.",
        "quality": "display_summary_only",
    }


def rewrite_matching_archive(snapshot: dict[str, Any]) -> None:
    dt = parse_dt(snapshot.get("generated_at"))
    if not dt:
        return
    path = ROOT / "data" / "events" / "archive" / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d") / f"{dt.strftime('%H%M%S')}Z.json"
    if path.exists():
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist; run monitor_events.py first")
    snapshot = json.loads(LATEST.read_text(encoding="utf-8"))
    canonical = snapshot.get("canonical_events") or []
    pop_path = ensure_population_source()
    enriched: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    diagnostics = {
        "wildfire_enriched": 0,
        "wildfire_polygon_missing": 0,
        "storm_enriched": 0,
        "storm_visible": 0,
        "storm_hidden_remote_or_unpopulated": 0,
    }
    with rasterio.open(pop_path) as src:
        for event in canonical:
            typ = event.get("type")
            if typ == "Wildfire":
                e, eligible = enrich_wildfire(event, src)
                diagnostics["wildfire_enriched"] += int(isinstance(e.get("exposure"), dict))
                diagnostics["wildfire_polygon_missing"] += int(e.get("exposure_status") == "wildfire_polygon_unavailable")
            elif typ == "Storm":
                e, eligible = enrich_storm(event, src)
                diagnostics["storm_enriched"] += int(isinstance(e.get("exposure"), dict))
                diagnostics["storm_visible"] += int(eligible)
                diagnostics["storm_hidden_remote_or_unpopulated"] += int(not eligible)
            else:
                e, eligible = deepcopy(event), True
            enriched.append(e)
            if eligible:
                visible.append(e)

    display = cluster_fires(visible)
    for event in display:
        enrich_cluster_exposure(event)

    snapshot["schema_version"] = "1.2"
    snapshot["canonical_events"] = enriched
    snapshot["events"] = display
    snapshot["hazard_exposure"] = {
        "calculated_at": now_iso(),
        "population_dataset": "JRC GHSL GHS-WUP-POP R2025A, epoch 2025, authoritative ~1 km source",
        "wildfire_rule": "burned area >= 10,000 ha; population in burned polygon and within 5 km",
        "storm_rule": "CEMS activation OR GDACS Orange/Red OR populated GDACS TC footprint; EONET-only fallback uses populated 300 km center buffer",
        "interpretation": "Potential population exposure; not observed impact, casualties, attribution, or loss",
        "diagnostics": diagnostics,
    }
    snapshot.setdefault("monitor", {})["storm_display_rule"] = snapshot["hazard_exposure"]["storm_rule"]
    snapshot["monitor"]["population_reference"] = "GHSL GHS-WUP-POP R2025A E2025 ~1 km"

    text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    LATEST.write_text(text, encoding="utf-8")
    rewrite_matching_archive(snapshot)
    print(json.dumps({
        "population_source": str(pop_path),
        "canonical_events": len(enriched),
        "display_events": len(display),
        "diagnostics": diagnostics,
    }, indent=2))


if __name__ == "__main__":
    main()
