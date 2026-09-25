# Frozen wildfire transport reference capsule

This directory is the public, immutable-by-content reference boundary for the
WILDFIRE_TRANSPORT_SCORE_V1 experiment.

The authoritative capsule consists of 69 event records:
- 45 frozen V4 development events;
- 24 frozen Satellite Validation V2 events.

Each record must be stored under `development45/` or `validation24/` and
listed in `manifest.sha256`. The manifest is the integrity authority: a replay
must refuse any missing file, extra event, or SHA-256 mismatch.

Security rules:
- no credential, token, service-account JSON, private key, email credential, or
  private Actions download URL is allowed in the capsule;
- provenance may name public-safe source run IDs and commit SHAs only;
- Earth Engine authentication is runtime-only and is not part of the capsule;
- validation outcomes are not inputs to development model selection.

The frozen lock and preserved 23/24 summary are adjacent JSON records. The
missing historical replay event remains gdacs-WF-1031562 until a complete
public replay is performed from this capsule.
