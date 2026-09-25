#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CAP=ROOT/"data/reference/wildfire_transport_score_v1"
MAN=CAP/"manifest.sha256"
def main():
    if not MAN.exists():
        print("FROZEN_CAPSULE=NOT_YET_MATERIALIZED")
        return 0
    rows=[]
    for raw in MAN.read_text(encoding="utf-8").splitlines():
        raw=raw.strip()
        if not raw or raw.startswith("#"): continue
        digest, rel=raw.split(None,1); rel=rel.strip().lstrip("*")
        p=CAP/rel
        if not p.is_file(): raise SystemExit(f"MISSING {rel}")
        got=hashlib.sha256(p.read_bytes()).hexdigest()
        if got!=digest: raise SystemExit(f"SHA256_MISMATCH {rel}")
        rows.append(rel)
    dev=[x for x in rows if x.startswith("development45/")]
    val=[x for x in rows if x.startswith("validation24/")]
    if len(dev)!=45 or len(val)!=24 or len(rows)!=69:
        raise SystemExit(f"COUNT_MISMATCH total={len(rows)} development={len(dev)} validation={len(val)}")
    print("FROZEN_CAPSULE_SHA256=PASS total=69 development=45 validation=24")
    return 0
if __name__=="__main__": raise SystemExit(main())
