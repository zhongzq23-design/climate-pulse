# Wildfire transport score public migration

This branch migrates the credential-free research implementation of `WILDFIRE_TRANSPORT_SCORE_V1` from the former private backend experiment into the public `climate-pulse` repository.

## Security boundary

- No Earth Engine service-account JSON, private key, GitHub token, or other credential is committed.
- The Python code reads Earth Engine credentials only from environment variables at runtime.
- The public workflow currently performs compile and embedded-secret checks only.
- Historical private Actions artifacts are **not** fetched from the public workflow. They remain evidence in the private repository until a separate, reviewed public frozen-input capsule is created.
- Any future Earth Engine compute workflow must use the existing protected `production-secrets` environment and repository/environment secrets, never committed values.

## Migrated research code

- continuous source-weighted transport score;
- frozen-event compatibility runner;
- independent satellite validation and spatial-block summarization;
- supporting wildfire trajectory/satellite modules;
- experiment protocol documentation.

## Current scientific state

The 45-fire development stage locked the transport-score configuration without using the 24-fire satellite outcomes. The independent validation remains an experimental research result and is not production exposure semantics.

This migration deliberately does not modify website wildfire population exposure, production event JSON, or public claims.
