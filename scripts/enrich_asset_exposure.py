#!/usr/bin/env python3
"""Add non-population asset exposure metrics to mapped Climate Pulse events.

Public interpretation is deliberately conservative:
- Drought: mapped area plus land, forest and crop area overlapping the polygon.
- Wildfire: forest area overlapping the mapped fire polygon.
- Flood / tropical cyclone: a GDP-exposure *proxy*, not economic loss, computed
  from GHSL 2025 residential population inside the mapped footprint by country
  multiplied by World Bank WDI 2024 GDP per capita (current US$).

The script reads unsimplified transient geometries produced earlier in the same
workflow by ``enrich_event_footprints.py``. It therefore never performs exposure
analysis on browser-simplified polygons.
"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Geod
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape

import enrich_hazard_exposure as h

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "events" / "latest.json"
RUNTIME_DIR = ROOT / ".runtime" / "event_geometries"
ASSET_DIR = ROOT / "data" / "exposure" / "assets"
POP_DIR = ROOT / "data" / "exposure" / "population"
LANDCOVER_TIF = ROOT / "data" / "reference" / "landcover" / "modis_mcd12c1_2024" / "land_forest_area_ha_0p05.tif"
CROP_TIF = ROOT / "data" / "reference" / "crops" / "cropgrids_2020" / "crop_area_ha_0p05.tif"

NE_COUNTRIES = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
WDI_GDP_PC_2024 = "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.PCAP.CD?date=2024&format=json&per_page=400"
GEOD = Geod(ellps="WGS84")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(event_id: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(event_id or "event"))[:160]


def load_runtime_geometry(event_id: Any) -> tuple[dict[str, Any] | None, Any]:
    p = RUNTIME_DIR / f"{safe_name(event_id)}.json"
    if not p.exists():
        return None, None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        geom = shape(doc.get("geometry"))
        if geom.is_empty:
            return None, None
        if not geom.is_valid:
            geom = geom.buffer(0)
        return doc, geom if not geom.is_empty else None
    except Exception:  # noqa: BLE001
        return None, None


def geodesic_area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(float(area_m2)) / 1_000_000.0
    except Exception:  # noqa: BLE001
        # Fall back to polygon-part accumulation for unusual collections.
        total = 0.0
        for g in getattr(geom, "geoms", []):
            try:
                a, _ = GEOD.geometry_area_perimeter(g)
                total += abs(float(a))
            except Exception:  # noqa: BLE001
                pass
        return total / 1_000_000.0


def raster_area_ha(path: Path, geom4326, band: int = 1) -> float | None:
    if not path.exists() or geom4326 is None or geom4326.is_empty:
        return None
    with rasterio.open(path) as src:
        src_geom = transform_geom("EPSG:4326", src.crs, mapping(geom4326), precision=7)
        try:
            arr, _ = mask(src, [src_geom], crop=True, filled=False, all_touched=False, indexes=band)
        except ValueError:
            return 0.0
        vals = arr.compressed().astype("float64", copy=False) if np.ma.isMaskedArray(arr) else np.asarray(arr, dtype="float64").ravel()
        vals = vals[np.isfinite(vals)]
        if src.nodata is not None:
            vals = vals[vals != float(src.nodata)]
        vals = vals[(vals >= 0) & (vals < 65000)]
        return float(vals.sum(dtype="float64"))


def load_world_bank_gdp_pc() -> dict[str, float]:
    data = h.fetch_json(WDI_GDP_PC_2024, timeout=45)
    rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("countryiso3code") or "").upper()
        try:
            val = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if len(code) == 3 and math.isfinite(val) and val >= 0:
            out[code] = val
    return out


def load_country_geometries() -> list[tuple[str, str, Any]]:
    fc = h.fetch_json(NE_COUNTRIES, timeout=60)
    out = []
    for f in fc.get("features", []) if isinstance(fc, dict) else []:
        if not isinstance(f, dict) or not isinstance(f.get("geometry"), dict):
            continue
        p = f.get("properties") or {}
        code_candidates = [p.get("WB_A3"), p.get("ISO_A3"), p.get("ADM0_A3")]
        code = next((str(c).upper() for c in code_candidates if c and str(c) != "-99"), "")
        if len(code) != 3:
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
        except Exception:  # noqa: BLE001
            continue
        name = str(p.get("NAME_LONG") or p.get("ADMIN") or p.get("NAME") or code)
        out.append((code, name, g))
    return out


def gdp_exposure_proxy(geom, pop_src, countries, gdp_pc) -> dict[str, Any] | None:
    if geom is None or geom.is_empty:
        return None
    known_pop = 0
    country_pop = 0
    value = 0.0
    detail = []
    minx, miny, maxx, maxy = geom.bounds
    for code, name, country_geom in countries:
        cminx, cminy, cmaxx, cmaxy = country_geom.bounds
        if cmaxx < minx or cminx > maxx or cmaxy < miny or cminy > maxy:
            continue
        try:
            if not geom.intersects(country_geom):
                continue
            inter = geom.intersection(country_geom)
        except Exception:  # noqa: BLE001
            continue
        if inter.is_empty:
            continue
        pop = h.raster_population(pop_src, inter)
        if pop <= 0:
            continue
        country_pop += pop
        pc = gdp_pc.get(code)
        if pc is None:
            continue
        known_pop += pop
        part = float(pop) * float(pc)
        value += part
        detail.append({"country": name, "iso3": code, "population_basis": int(pop), "gdp_per_capita_usd_2024": round(pc, 2), "proxy_usd": round(part)})
    if known_pop <= 0:
        return None
    coverage = 100.0 if country_pop <= 0 else known_pop / country_pop * 100.0
    detail.sort(key=lambda x: x["proxy_usd"], reverse=True)
    return {
        "potential_gdp_exposure_proxy_usd": round(value),
        "gdp_proxy_reference_year": 2024,
        "gdp_proxy_indicator": "World Bank WDI NY.GDP.PCAP.CD (GDP per capita, current US$)",
        "gdp_proxy_population_reference": "JRC GHSL GHS-WUP-POP R2025A, epoch 2025",
        "gdp_proxy_population_coverage_pct": round(coverage, 1),
        "gdp_proxy_method": "sum over countries: GHSL population inside hazard footprint × WDI 2024 GDP per capita",
        "gdp_proxy_country_components": detail[:12],
        "gdp_proxy_interpretation": "Economic activity exposure proxy only; not observed loss, asset damage, business interruption, or a gridded local-GDP estimate.",
    }


def write_asset_doc(event: dict[str, Any]) -> None:
    x = event.get("exposure")
    if not isinstance(x, dict):
        return
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": "1.0",
        "event_id": event.get("id"),
        "event_type": event.get("type"),
        "title": event.get("title"),
        "region": event.get("region"),
        "calculated_at": now_iso(),
        "exposure": x,
    }
    (ASSET_DIR / f"{safe_name(event.get('id'))}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def purge_drought_population_file(event: dict[str, Any]) -> None:
    p = POP_DIR / f"{safe_name(event.get('id'))}.json"
    if p.exists():
        p.unlink()


def enrich_canonical(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    need_gdp = any(e.get("type") in {"Flood", "Storm"} for e in events)
    pop_path = h.ensure_population_source() if need_gdp else None
    countries = load_country_geometries() if need_gdp else []
    gdp_pc = load_world_bank_gdp_pc() if need_gdp else {}
    pop_src = rasterio.open(pop_path) if pop_path else None

    diag = {
        "drought_mapped": 0,
        "drought_landcover_ready": 0,
        "drought_crop_ready": 0,
        "wildfire_forest_ready": 0,
        "flood_gdp_ready": 0,
        "storm_gdp_ready": 0,
    }
    out = []
    try:
        for original in events:
            event = deepcopy(original)
            typ = event.get("type")
            runtime, geom = load_runtime_geometry(event.get("id"))
            x = deepcopy(event.get("exposure")) if isinstance(event.get("exposure"), dict) else {}

            if typ == "Drought":
                purge_drought_population_file(event)
                if geom is not None and runtime:
                    x = {
                        "hazard": "drought",
                        "gdacs_event_id": runtime.get("gdacs_event_id"),
                        "gdacs_episode_id": runtime.get("gdacs_episode_id"),
                        "footprint_method": runtime.get("footprint_method") or "GDACS drought event polygon",
                        "mapped_footprint_area_km2": round(geodesic_area_km2(geom), 1),
                        "quality": "mapped_event_footprint",
                        "interpretation": "Spatial overlap only; area inside the drought polygon is not proof of ecological or agricultural damage.",
                    }
                    diag["drought_mapped"] += 1
                    if LANDCOVER_TIF.exists():
                        land_ha = raster_area_ha(LANDCOVER_TIF, geom, 1)
                        forest_ha = raster_area_ha(LANDCOVER_TIF, geom, 2)
                        if land_ha is not None:
                            x["land_area_in_footprint_km2"] = round(land_ha / 100.0, 1)
                        if forest_ha is not None:
                            x["forest_area_in_footprint_km2"] = round(forest_ha / 100.0, 1)
                        x["landcover_reference"] = "NASA MODIS MCD12C1.061 2024, IGBP sub-pixel class percentages, 0.05°"
                        diag["drought_landcover_ready"] += 1
                    if CROP_TIF.exists():
                        crop_ha = raster_area_ha(CROP_TIF, geom, 1)
                        if crop_ha is not None:
                            x["crop_area_in_footprint_km2"] = round(crop_ha / 100.0, 1)
                        x["crop_reference"] = "FAO CROPGRIDS v1.08 2020 crop physical area, 0.05°"
                        diag["drought_crop_ready"] += 1
                    x["reference_status"] = {
                        "landcover": "ready" if LANDCOVER_TIF.exists() else "reference_not_prepared",
                        "crops": "ready" if CROP_TIF.exists() else "reference_not_prepared",
                    }
                    event["exposure"] = x
                    event["exposure_status"] = "ready"

            elif typ == "Wildfire" and geom is not None and LANDCOVER_TIF.exists():
                forest_ha = raster_area_ha(LANDCOVER_TIF, geom, 2)
                if forest_ha is not None:
                    x["forest_area_in_wildfire_footprint_km2"] = round(forest_ha / 100.0, 1)
                    x["forest_reference"] = "NASA MODIS MCD12C1.061 2024, IGBP forest classes 1–5, 0.05°"
                    event["exposure"] = x
                    diag["wildfire_forest_ready"] += 1

            elif typ in {"Flood", "Storm"} and geom is not None and runtime and pop_src is not None:
                proxy = gdp_exposure_proxy(geom, pop_src, countries, gdp_pc)
                if proxy:
                    if typ == "Flood" and not x:
                        x = {
                            "hazard": "flood",
                            "gdacs_event_id": runtime.get("gdacs_event_id"),
                            "gdacs_episode_id": runtime.get("gdacs_episode_id"),
                            "footprint_method": runtime.get("footprint_method") or "GDACS flood event polygon",
                            "quality": "mapped_event_footprint",
                        }
                    x.update(proxy)
                    event["exposure"] = x
                    if typ == "Flood":
                        diag["flood_gdp_ready"] += 1
                    else:
                        diag["storm_gdp_ready"] += 1

            if isinstance(event.get("exposure"), dict) and any(k in event["exposure"] for k in (
                "mapped_footprint_area_km2", "forest_area_in_wildfire_footprint_km2",
                "potential_gdp_exposure_proxy_usd"
            )):
                write_asset_doc(event)
            out.append(event)
    finally:
        if pop_src is not None:
            pop_src.close()
    return out, diag


def rebuild_display(display: list[dict[str, Any]], canonical: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(e.get("id")): e for e in canonical}
    out = []
    for original in display:
        event = deepcopy(original)
        key = str(event.get("id"))
        if key in by_id:
            # Preserve browser footprint added by the footprint step while copying
            # the newly enriched canonical event fields.
            fp = event.get("footprint")
            event = deepcopy(by_id[key])
            if fp:
                event["footprint"] = fp
        elif event.get("type") == "Wildfire" and isinstance(event.get("members"), list):
            members = []
            forest = 0.0
            forest_n = 0
            for m in event["members"]:
                mm = deepcopy(by_id.get(str(m.get("id")), m))
                members.append(mm)
                fx = mm.get("exposure") or {}
                try:
                    forest += float(fx.get("forest_area_in_wildfire_footprint_km2"))
                    forest_n += 1
                except (TypeError, ValueError):
                    pass
            event["members"] = members
            if forest_n:
                x = deepcopy(event.get("exposure")) if isinstance(event.get("exposure"), dict) else {"hazard": "wildfire_cluster"}
                x["forest_area_member_sum_km2"] = round(forest, 1)
                x["forest_area_member_sum_note"] = "Member-footprint sum; overlaps can be counted more than once."
                event["exposure"] = x
        out.append(event)
    return out


def main() -> None:
    if not LATEST.exists():
        raise RuntimeError("data/events/latest.json does not exist")
    snap = json.loads(LATEST.read_text(encoding="utf-8"))
    canonical, diag = enrich_canonical(snap.get("canonical_events") or [])
    display = rebuild_display(snap.get("events") or [], canonical)
    snap["canonical_events"] = canonical
    snap["events"] = display
    snap.setdefault("asset_exposure", {})["calculated_at"] = now_iso()
    snap["asset_exposure"].update({
        "drought": "mapped area; MODIS 2024 land/forest area and FAO CROPGRIDS 2020 crop physical area when prepared",
        "wildfire": "MODIS 2024 forest area within mapped wildfire footprint when prepared",
        "flood_and_tc_gdp": "GHSL 2025 population within country-split mapped footprint × World Bank WDI 2024 GDP per capita; exposure proxy only",
        "diagnostics": diag,
    })
    snap.setdefault("monitor", {})["asset_exposure"] = snap["asset_exposure"]
    LATEST.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    h.rewrite_matching_archive(snap)
    print(json.dumps({
        "status": "ok",
        "landcover_reference_ready": LANDCOVER_TIF.exists(),
        "crop_reference_ready": CROP_TIF.exists(),
        "diagnostics": diag,
    }, indent=2))


if __name__ == "__main__":
    main()
