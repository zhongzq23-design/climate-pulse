#!/usr/bin/env python3
"""Climate Pulse background event monitor.

Runs three times per day, ingests NASA EONET, GDACS and Copernicus CEMS,
normalizes source records, separates source-update/event-start/event-end timing,
and stores timestamped raw snapshots before downstream enrichment.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import ssl
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "events"
LATEST_PATH = DATA_DIR / "latest.json"
LIFECYCLE_PATH = DATA_DIR / "lifecycle.json"

EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
CEMS_URL = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/"
CEMS_DETAIL_URL = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/"
WILDFIRE_MIN_HA = 10_000.0
WINDOW_DAYS = 7
USER_AGENT = "ClimatePulse/0.1 (+https://zhongzq23-design.github.io/climate-pulse/)"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def point_wkt(value: Any) -> dict[str, float] | None:
    m = re.search(r"POINT\s*\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)", str(value or ""), re.I)
    if not m:
        return None
    return {"lon": float(m.group(1)), "lat": float(m.group(2))}


def centroid(geom: Any) -> dict[str, float] | None:
    if not isinstance(geom, dict):
        return None
    if geom.get("type") == "Point" and isinstance(geom.get("coordinates"), list):
        c = geom["coordinates"]
        if len(c) >= 2:
            return {"lon": float(c[0]), "lat": float(c[1])}
    pts: list[tuple[float, float]] = []

    def walk(v: Any) -> None:
        if not isinstance(v, list):
            return
        if len(v) >= 2 and isinstance(v[0], (int, float)) and isinstance(v[1], (int, float)):
            pts.append((float(v[0]), float(v[1])))
            return
        for item in v:
            walk(item)

    walk(geom.get("coordinates"))
    if not pts:
        return None
    return {"lon": sum(p[0] for p in pts) / len(pts), "lat": sum(p[1] for p in pts) / len(pts)}


def valid_point(p: dict[str, float] | None) -> bool:
    return bool(p and math.isfinite(p["lon"]) and math.isfinite(p["lat"]) and abs(p["lon"]) <= 180 and abs(p["lat"]) <= 90)


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


def date_label(value: Any, now: datetime) -> str:
    dt = parse_dt(value)
    if not dt or dt > now + timedelta(minutes=5):
        return "Update time unavailable"
    hours = max(0, round((now - dt).total_seconds() / 3600))
    if hours < 1:
        return "Updated <1 hour ago"
    if hours < 24:
        return f"Updated {hours} h ago"
    days = round(hours / 24)
    return f"Updated {days} day{'s' if days != 1 else ''} ago"


def first_value(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return None


def truncate(value: Any, n: int = 480) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def category_eonet(c: Any) -> str | None:
    if not isinstance(c, dict):
        return None
    s = str(c.get("id") or c.get("title") or "").lower()
    if "severestorm" in s:
        return "Storm"
    if "flood" in s:
        return "Flood"
    if "drought" in s:
        return "Drought"
    if "tempextreme" in s or "temperature" in s:
        return "Heat"
    if "landslide" in s:
        return "Landslide"
    return None


def category_cems(c: Any) -> str | None:
    if isinstance(c, dict):
        c = c.get("slug") or c.get("name") or c.get("title") or ""
    s = str(c or "").lower()
    if "fire" in s:
        return "Wildfire"
    if "flood" in s:
        return "Flood"
    if any(x in s for x in ("storm", "cyclone", "hurricane", "typhoon")):
        return "Storm"
    if "drought" in s:
        return "Drought"
    if "landslide" in s or "mass movement" in s:
        return "Landslide"
    if "heat" in s or "temperature" in s:
        return "Heat"
    return None


def category_gdacs(c: Any) -> str | None:
    return {"FL": "Flood", "TC": "Storm", "WF": "Wildfire", "DR": "Drought"}.get(str(c or "").upper())


def parse_ha(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        n = float(value)
        return n if n >= 1000 else None
    s = str(value)
    m = re.search(r"([0-9][0-9.,\s]*)\s*(?:ha|hectares?)", s, re.I)
    if m:
        try:
            return float(re.sub(r"[\s,]", "", m.group(1)))
        except ValueError:
            return None
    try:
        n = float(s.replace(",", ""))
        return n if n >= 1000 else None
    except ValueError:
        return None


def burned_ha(p: dict[str, Any]) -> float | None:
    severity = p.get("severitydata") or {}
    values = [
        p.get("ha"), p.get("hectares"), p.get("burnedarea"), p.get("burnedArea"), p.get("burned_area"),
        p.get("burnedarea_ha"), p.get("burnedAreaHa"), p.get("area_ha"), severity.get("ha"), severity.get("hectares"),
        severity.get("burnedarea"), severity.get("burnedArea"), severity.get("severitytext"), severity.get("severity"),
    ]
    for value in values:
        n = parse_ha(value)
        if n is not None:
            return n
    return parse_ha(json.dumps(severity, ensure_ascii=False))


def priority(level: Any) -> str:
    s = str(level or "").lower()
    return "High" if s == "red" else "Medium" if s == "orange" else "Standard"


def parse_eonet(data: Any, now: datetime) -> list[dict[str, Any]]:
    out = []
    for e in data.get("events", []) if isinstance(data, dict) else []:
        cats = e.get("categories") or []
        typ = category_eonet(cats[0] if cats else None)
        if not typ:
            continue
        geoms = e.get("geometry") or []
        g = geoms[-1] if geoms else None
        p = centroid(g)
        if not valid_point(p):
            continue
        sources = e.get("sources") or []
        url = next((x.get("url") for x in sources if isinstance(x, dict) and x.get("url")), None) or e.get("link") or EONET_URL
        latest = g.get("date") if isinstance(g, dict) else None
        start = geoms[0].get("date") if geoms and isinstance(geoms[0], dict) else None
        out.append({
            "id": f"eonet-{e.get('id')}", "source_id": str(e.get("id")), "origin": "eonet",
            "title": e.get("title") or f"{typ} event", "type": typ,
            "region": f"{p['lat']:.2f}°, {p['lon']:.2f}°", "lat": p["lat"], "lon": p["lon"],
            "status": "Open", "updated": date_label(latest, now), "priority": "Standard", "climate_link": "Not assessed",
            "summary": truncate(e.get("description") or f"{typ} event tracked by NASA EONET."),
            "source": "NASA EONET", "source_url": url, "event_date": latest,
            "source_updated_at": latest, "event_start": start, "event_end": None, "last_detection": latest,
        })
    return out


def country_name(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return str(x.get("short_name") or x.get("name") or x.get("title") or "")
    return ""


def parse_cems(data: Any, now: datetime) -> list[dict[str, Any]]:
    out = []
    for e in data.get("results", []) if isinstance(data, dict) else []:
        if e.get("closed"):
            continue
        typ = category_cems(e.get("category"))
        p = point_wkt(e.get("centroid"))
        if not typ or not valid_point(p):
            continue
        countries = " · ".join(filter(None, (country_name(x) for x in (e.get("countries") or []))))
        region = countries or f"{p['lat']:.2f}°, {p['lon']:.2f}°"
        code = str(e.get("code") or "")
        detail = f"{CEMS_DETAIL_URL}?{urllib.parse.urlencode({'code': code})}" if code else CEMS_URL
        source_updated = e.get("lastUpdate")
        event_start = e.get("eventTime") or e.get("activationTime")
        event_date = source_updated or event_start
        fallback_id = f"{p['lat']:.2f}-{p['lon']:.2f}"
        summary = f"Copernicus EMS Rapid Mapping activation{f' {code}' if code else ''}."
        if e.get("gdacsId"):
            summary += f" Linked GDACS ID: {e.get('gdacsId')}."
        out.append({
            "id": f"cems-{code or fallback_id}", "source_id": code or "unknown", "origin": "cems",
            "title": e.get("name") or f"{typ} activation", "type": typ, "region": region, "lat": p["lat"], "lon": p["lon"],
            "status": "Active", "updated": date_label(source_updated, now), "priority": "Review", "climate_link": "Not assessed",
            "summary": truncate(summary), "source": "Copernicus CEMS", "source_url": detail, "event_date": event_date,
            "source_updated_at": source_updated, "event_start": event_start, "event_end": None, "last_detection": source_updated,
            "gdacs_id": e.get("gdacsId"),
        })
    return out


def parse_gdacs(data: Any, now: datetime, diag: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for f in data.get("features", []) if isinstance(data, dict) else []:
        p = centroid(f.get("geometry"))
        q = f.get("properties") or {}
        typ = category_gdacs(q.get("eventtype"))
        if not typ or not valid_point(p):
            continue
        ha = None
        if typ == "Wildfire":
            diag["wildfire_raw"] += 1
            ha = burned_ha(q)
            if ha is not None:
                diag["wildfire_with_area"] += 1
            if ha is None:
                diag["wildfire_area_unknown"] += 1
                continue
            if ha < WILDFIRE_MIN_HA:
                diag["wildfire_below_threshold"] += 1
                continue
            diag["wildfire_major"] += 1
        eid = str(q.get("eventid") if q.get("eventid") is not None else f"{p['lat']:.2f}-{p['lon']:.2f}-{q.get('todate') or ''}")
        region = str(q.get("country") or q.get("iso3") or f"{p['lat']:.2f}°, {p['lon']:.2f}°")
        sevdata = q.get("severitydata") or {}
        sev = sevdata.get("severitytext") or sevdata.get("severity") or ""
        event_start = q.get("fromdate")
        event_end = q.get("todate")
        source_updated = first_value(q, ("lastupdate", "lastUpdate", "datemodified", "dateModified", "modified", "updated", "publicationdate", "publicationDate"))
        last_detection = event_end if typ == "Wildfire" else None
        operational = source_updated or last_detection or event_start or event_end
        label_time = source_updated or last_detection
        summary = f"{'Burned area: ' + format(round(ha), ',') + ' ha. ' if ha is not None else ''}{sev or typ + ' event tracked by GDACS.'}{' Alert level: ' + str(q.get('alertlevel')) + '.' if q.get('alertlevel') else ''}"
        out.append({
            "id": f"gdacs-{q.get('eventtype') or 'EV'}-{eid}", "source_id": eid, "origin": "gdacs",
            "title": q.get("name") or f"{typ} in {region}", "type": typ, "region": region, "lat": p["lat"], "lon": p["lon"],
            "status": "Recent", "updated": date_label(label_time, now), "priority": priority(q.get("alertlevel")), "climate_link": "Not assessed",
            "summary": truncate(summary), "source": f"GDACS{' · ' + str(q.get('alertlevel')) if q.get('alertlevel') else ''}",
            "source_url": f"https://www.gdacs.org/report.aspx?{urllib.parse.urlencode({'eventid': eid, 'eventtype': q.get('eventtype') or ''})}",
            "event_date": operational, "source_updated_at": source_updated, "event_start": event_start, "event_end": event_end,
            "last_detection": last_detection, "burned_area_ha": ha,
        })
    return out


def distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    r = 6371.0
    d = math.pi / 180.0
    dl = (float(b["lat"]) - float(a["lat"])) * d
    dn = (float(b["lon"]) - float(a["lon"])) * d
    l1, l2 = float(a["lat"]) * d, float(b["lat"]) * d
    x = math.sin(dl / 2) ** 2 + math.cos(l1) * math.cos(l2) * math.sin(dn / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def day_gap(a: dict[str, Any], b: dict[str, Any]) -> float:
    x, y = parse_dt(a.get("event_date")), parse_dt(b.get("event_date"))
    return 999.0 if not x or not y else abs((x - y).total_seconds()) / 86400.0


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def stamp(e: dict[str, Any]) -> float:
        dt = parse_dt(e.get("event_date"))
        return dt.timestamp() if dt else 0.0

    out: list[dict[str, Any]] = []
    for e in sorted(events, key=stamp, reverse=True):
        match = next((x for x in out if x["type"] == e["type"] and distance(x, e) < 80 and day_gap(x, e) <= 5), None)
        if not match:
            x = deepcopy(e)
            x["source_urls"] = [e["source_url"]] if e.get("source_url") else []
            x["source_members"] = [{"origin": e["origin"], "source_id": e["source_id"]}]
            out.append(x)
            continue
        match["source"] = " · ".join(dict.fromkeys([match["source"], e["source"]]))
        match["source_urls"] = list(dict.fromkeys([*(match.get("source_urls") or []), *([e["source_url"]] if e.get("source_url") else [])]))
        match.setdefault("source_members", []).append({"origin": e["origin"], "source_id": e["source_id"]})
        if len(e.get("summary") or "") > len(match.get("summary") or ""):
            match["summary"] = e["summary"]
        if e.get("burned_area_ha") is not None and (match.get("burned_area_ha") is None or float(e["burned_area_ha"]) > float(match["burned_area_ha"])):
            match["burned_area_ha"] = e["burned_area_ha"]
        if stamp(e) >= stamp(match):
            for key in ("event_date", "source_updated_at", "event_start", "event_end", "last_detection", "updated", "status"):
                if e.get(key) is not None:
                    match[key] = e.get(key)
    return out


def cluster_fires(events: list[dict[str, Any]], radius: float = 180.0) -> list[dict[str, Any]]:
    other = [e for e in events if e["type"] != "Wildfire"]
    fires = [e for e in events if e["type"] == "Wildfire"]
    groups: list[list[dict[str, Any]]] = []
    for fire in fires:
        chosen = None
        for group in groups:
            center = {"lat": sum(x["lat"] for x in group) / len(group), "lon": sum(x["lon"] for x in group) / len(group)}
            if distance(center, fire) <= radius and day_gap(group[0], fire) <= 7:
                chosen = group
                break
        if chosen is None:
            groups.append([fire])
        else:
            chosen.append(fire)
    clustered = []
    for group in groups:
        if len(group) == 1:
            clustered.append(group[0])
            continue
        latest = max(group, key=lambda x: parse_dt(x.get("event_date")) or datetime(1970, 1, 1, tzinfo=timezone.utc))
        lat = sum(x["lat"] for x in group) / len(group)
        lon = sum(x["lon"] for x in group) / len(group)
        member_keys = sorted(f"{x.get('origin')}:{x.get('source_id')}" for x in group)
        cluster_hash = hashlib.sha1("|".join(member_keys).encode()).hexdigest()[:10]
        clustered.append({
            "id": f"wf-cluster-{cluster_hash}", "source_id": ",".join(x["source_id"] for x in group), "origin": "cluster",
            "title": f"Regional wildfire cluster · {len(group)} reports", "type": "Wildfire",
            "region": " · ".join(list(dict.fromkeys(x["region"] for x in group))[:3]), "lat": lat, "lon": lon,
            "status": "Clustered", "updated": latest["updated"],
            "priority": "High" if any(x["priority"] == "High" for x in group) else "Medium" if any(x["priority"] == "Medium" for x in group) else "Standard",
            "climate_link": "Not assessed", "summary": f"{len(group)} nearby major-wildfire records are grouped for the world overview. Expand this event to inspect individual source records.",
            "source": " · ".join(dict.fromkeys(x["source"].split(" · ")[0] for x in group)), "source_url": group[0].get("source_url"),
            "source_urls": list(dict.fromkeys(x.get("source_url") for x in group if x.get("source_url"))), "event_date": latest.get("event_date"),
            "source_updated_at": latest.get("source_updated_at"),
            "event_start": min((x.get("event_start") for x in group if x.get("event_start")), default=None),
            "event_end": latest.get("event_end"), "last_detection": latest.get("last_detection"),
            "burned_area_ha": sum(float(x.get("burned_area_ha") or 0) for x in group) or None, "member_count": len(group), "members": group,
        })
    all_events = other + clustered
    all_events.sort(key=lambda x: (parse_dt(x.get("event_date")) or datetime(1970, 1, 1, tzinfo=timezone.utc)), reverse=True)
    return all_events


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def carry_forward(previous: dict[str, Any], origin: str) -> list[dict[str, Any]]:
    return [deepcopy(x) for x in previous.get("source_events", []) if x.get("origin") == origin]


def source_call(name: str, fn, previous: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        rows = fn()
        return rows, {"status": "ok", "relevant_count": len(rows), "error": None}
    except Exception as exc:
        old = carry_forward(previous, name)
        return old, {
            "status": "stale_carry_forward" if old else "failed",
            "relevant_count": len(old),
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def update_lifecycle(source_events: list[dict[str, Any]], now_iso: str) -> dict[str, Any]:
    state = load_json(LIFECYCLE_PATH, {"schema_version": "2.0", "events": {}})
    state["schema_version"] = "2.0"
    table = state.setdefault("events", {})
    for e in source_events:
        key = e["id"]
        rec = table.get(key)
        if not rec:
            rec = {"first_seen": now_iso, "observations": 0}
            table[key] = rec
        rec.update({
            "last_seen": now_iso, "observations": int(rec.get("observations", 0)) + 1,
            "origin": e.get("origin"), "source_id": e.get("source_id"), "type": e.get("type"), "title": e.get("title"),
            "lat": e.get("lat"), "lon": e.get("lon"), "latest_status": e.get("status"), "latest_event_date": e.get("event_date"),
            "latest_source_updated_at": e.get("source_updated_at"), "latest_event_start": e.get("event_start"),
            "latest_event_end": e.get("event_end"), "latest_last_detection": e.get("last_detection"),
            "latest_burned_area_ha": e.get("burned_area_ha"),
        })
    state["updated_at"] = now_iso
    return state


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    now_iso = iso(now)
    previous = load_json(LATEST_PATH, {})
    diag = {"wildfire_raw": 0, "wildfire_with_area": 0, "wildfire_major": 0, "wildfire_below_threshold": 0, "wildfire_area_unknown": 0}
    from_day = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    to_day = now.date().isoformat()

    eonet_url = EONET_URL + "?" + urllib.parse.urlencode({
        "status": "open", "days": WINDOW_DAYS, "limit": 200,
        "category": "severeStorms,floods,drought,tempExtremes,landslides",
    })
    gdacs_base = {"fromDate": from_day, "toDate": to_day, "alertlevel": "Green;Orange;Red"}
    gdacs_nonfire = GDACS_URL + "?" + urllib.parse.urlencode({**gdacs_base, "eventlist": "FL,TC,DR"})
    gdacs_fire = GDACS_URL + "?" + urllib.parse.urlencode({**gdacs_base, "eventlist": "WF"})
    cems_url = CEMS_URL + "?" + urllib.parse.urlencode({"limit": 1000})

    eonet, s_eonet = source_call("eonet", lambda: parse_eonet(fetch_json(eonet_url), now), previous)
    gdacs, s_gdacs = source_call("gdacs", lambda: parse_gdacs(fetch_json(gdacs_nonfire), now, diag) + parse_gdacs(fetch_json(gdacs_fire), now, diag), previous)
    cems, s_cems = source_call("cems", lambda: parse_cems(fetch_json(cems_url), now), previous)

    source_events = eonet + gdacs + cems
    canonical_events = dedupe(source_events)
    display_events = cluster_fires(canonical_events)
    snapshot = {
        "schema_version": "2.0",
        "generated_at": now_iso,
        "monitor": {
            "cadence": "3x daily", "schedule_utc": ["00:17", "08:17", "16:17"], "window_days": WINDOW_DAYS,
            "wildfire_min_burned_area_ha": WILDFIRE_MIN_HA,
            "cems_endpoint": CEMS_URL,
            "temporal_model": "source_updated_at, event_start, event_end and last_detection are stored separately; display update age never uses a future event end",
        },
        "source_status": {"eonet": s_eonet, "gdacs": s_gdacs, "cems": s_cems},
        "diagnostics": diag,
        "source_events": source_events,
        "canonical_events": canonical_events,
        "events": display_events,
    }

    archive_path = DATA_DIR / "archive" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d") / f"{now.strftime('%H%M%S')}Z.json"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    latest_text = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    LATEST_PATH.write_text(latest_text, encoding="utf-8")
    archive_path.write_text(latest_text, encoding="utf-8")
    lifecycle = update_lifecycle(source_events, now_iso)
    LIFECYCLE_PATH.write_text(json.dumps(lifecycle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "generated_at": now_iso,
        "source_status": snapshot["source_status"],
        "source_events": len(source_events),
        "canonical_events": len(canonical_events),
        "display_events": len(display_events),
        "archive": str(archive_path.relative_to(ROOT)),
        "diagnostics": diag,
    }, indent=2))


if __name__ == "__main__":
    main()
