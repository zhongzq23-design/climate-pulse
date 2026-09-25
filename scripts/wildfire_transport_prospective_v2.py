#!/usr/bin/env python3
"""Prospective, outcome-blind wildfire transport validation.

Phase 1 freezes predictions from the current public wildfire universe before
satellite outcomes are inspected. Phase 2 validates only already-frozen
predictions. Development parameters are never retuned here.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import mapping
import probe_wildfire_downwind as p
import probe_wildfire_downwind_satellite_validation as sat
import wildfire_transport_score_v1 as score

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"data/reference/wildfire_transport_score_v1/locked_config.json"

def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()

def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def dump(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def select_current(snapshot,max_events,min_area):
    vals=snapshot.get("canonical_events") or snapshot.get("events") or []
    out=[]
    for e in vals:
        if not isinstance(e,dict) or e.get("type")!="Wildfire": continue
        fp=e.get("footprint") if isinstance(e.get("footprint"),dict) else {}
        qc=e.get("footprint_qc") if isinstance(e.get("footprint_qc"),dict) else {}
        try: area=float(e.get("burned_area_ha") or 0); float(e["lat"]); float(e["lon"])
        except (TypeError,ValueError,KeyError): continue
        if area<min_area or fp.get("status")!="ready" or qc.get("status")!="pass": continue
        out.append(e)
    out.sort(key=lambda x:(float(x.get("burned_area_ha") or 0),str(x.get("id"))),reverse=True)
    return out[:max_events]

def freeze(args):
    snap=load(ROOT/"data/events/latest.json"); locked=load(LOCK)
    events=select_current(snap,args.max_events,args.min_area_ha)
    if not events: raise SystemExit("no eligible current wildfires")
    ee,_project=p.initialize_ee(); now=datetime.now(timezone.utc)
    records=[]
    for e in events:
        try:
            perimeter,source,episode=p.load_event_perimeter(e)
            seeds,seed_meta=p.collect_fire_seeds(ee,e,perimeter,now,250.0,12)
            run,creation_ms,base_ms,leads,target_h=sat.choose_ifs_run(ee,now,12)
            tracks,wind=sat.simulate_100m(ee,run,leads,seeds)
            baseline=p.corridor_geometry(tracks,e,50.0)
            params=locked.get("parameters") or {}
            cfg={"config_id":locked["locked_config_id"],"sigma_km":params["sigma_km"],"travel_tau_h":params["travel_tau_h"],"frp_weighting":params["frp_weighting"],"competitor_masking":params["competitor_masking"]}
            scored,score_meta=score.score_geometry(e,seeds,tracks,leads,baseline,cfg,now,competitor_wgs=None)
            payload={
              "event":{"id":e["id"],"lat":e["lat"],"lon":e["lon"],"burned_area_ha":e.get("burned_area_ha")},
              "frozen_at":p.iso(now),
              "perimeter_source":source,"episode":episode,
              "seeds":seeds,"seed_meta":seed_meta,
              "ifs":{"creation_time":p.millis_to_iso(creation_ms),"base_time":p.millis_to_iso(base_ms),"leads_used_hours":leads,"target_hours":target_h,"wind_stats":wind},
              "baseline":{"family":"V4b","wind":"IFS 100 m","max_horizon_h":12,"buffer_km":50,"geometry":mapping(baseline)},
              "transport_score":{"lock":locked,"geometry":mapping(scored),"geometry_meta":score_meta},
              "guardrail":"Outcome-blind prospective prediction freeze; no satellite outcome inspected and no retuning authorized."
            }
            payload["prediction_sha256"]=digest(payload)
            records.append(payload)
        except Exception as exc:
            records.append({"event":{"id":e.get("id")},"status":"excluded","reason":f"{type(exc).__name__}: {str(exc)[:800]}"})
    doc={"schema_version":"2.0","experiment":"WILDFIRE_TRANSPORT_PROSPECTIVE_V2","phase":"prediction_freeze","frozen_at":p.iso(now),"records":records}
    dump(args.output,doc)
    print(json.dumps({"selected":len(events),"frozen":sum("prediction_sha256" in x for x in records),"excluded":sum(x.get("status")=="excluded" for x in records),"output":args.output},indent=2))
    return 0

def verify(args):
    doc=load(args.input); good=0
    for row in doc.get("records") or []:
        expected=row.get("prediction_sha256")
        if not expected: continue
        copy=dict(row); copy.pop("prediction_sha256",None)
        if digest(copy)!=expected: raise SystemExit(f"prediction hash mismatch: {(row.get('event') or {}).get('id')}")
        good+=1
    if not good: raise SystemExit("no frozen predictions")
    print(f"PROSPECTIVE_FREEZE_VERIFY=PASS records={good}")
    return 0

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    q=sub.add_parser("freeze"); q.add_argument("--output",required=True); q.add_argument("--max-events",type=int,default=12); q.add_argument("--min-area-ha",type=float,default=10000); q.set_defaults(func=freeze)
    q=sub.add_parser("verify"); q.add_argument("--input",required=True); q.set_defaults(func=verify)
    a=ap.parse_args(); return a.func(a)
if __name__=="__main__": raise SystemExit(main())
