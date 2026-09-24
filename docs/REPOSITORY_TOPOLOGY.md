# Climate Pulse repository topology

Status: **public-monorepo cutover target**  
Effective date: 2026-09-24

## Canonical production repository

### `zhongzq23-design/climate-pulse` — public operational authority

This repository owns:

- Python/GEE processing and ingestion code
- tests and QC logic
- scheduled event/report/reference workflows
- generated data and reports
- website HTML/CSS/browser JavaScript
- GitHub Pages deployment

Secrets remain in GitHub Actions secret storage; public source code contains
only secret names and safe configuration logic.

### `zhongzq23-design/climate-pulse-backend`

After cutover this private repository is a rollback/archive surface.  It must
not remain an independent scheduled production writer, because dual writers
would create divergent state and race conditions.

### `zhongzq23-design/climate-pulse-legacy-private`

Historical archive only; no production schedules.

## Production flow

```text
External sources / Earth Engine
        ↓
climate-pulse (public source + processing)
        ↓
QC / enrichment / reports
        ↓
same-repository commit with GITHUB_TOKEN
        ↓
scripts/build_public_dist.py --check
        ↓
GitHub Pages filtered artifact
```

## Security invariants

1. Secrets/credentials are never committed.
2. Pull-request workflows are secret-free and `contents: read`.
3. Secret-dependent workflows do not use `pull_request` or `pull_request_target`.
4. `pull_request_target` is prohibited for repository-code execution.
5. Production writers request only `contents: write`.
6. The former `PUBLIC_REPO_TOKEN` cross-repository publication dependency is retired.
7. No workflow may dump the complete environment, enable xtrace, or echo secret values.
8. All production writers share a serialization lock where they can touch overlapping products.
9. Pages uploads only `public-dist`, never the whole source tree.

## Cutover rule

The public migration branch must pass secret-free PR validation before merge.
Secret-dependent production workflows are validated only from trusted
main/manual contexts.  Once the public production path is proven, competing
production automation in `climate-pulse-backend` should remain disabled.
