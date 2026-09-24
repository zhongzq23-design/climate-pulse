# Climate Pulse

Climate Pulse is a public operational monorepo: the GitHub Pages site, generated
event/report products, Python processing code, QC logic, regression tests and
GitHub Actions workflows live together in this repository.

## Security model

- Secrets are never committed. Secret-dependent jobs read only repository
  Actions secrets/variables.
- Pull-request validation is secret-free and read-only.
- Production jobs that commit generated products use the repository-scoped
  `GITHUB_TOKEN` with `contents: write`.
- The former cross-repository `PUBLIC_REPO_TOKEN` publication path is retired.
- GitHub Pages publishes only the filtered artifact produced by
  `scripts/build_public_dist.py`; backend source files are not part of the Pages artifact.

Repository roles after cutover:

- `zhongzq23-design/climate-pulse` — public production source and Pages repository.
- `zhongzq23-design/climate-pulse-backend` — private rollback/archive during cutover; no longer the intended production writer after migration is accepted.
- `zhongzq23-design/climate-pulse-legacy-private` — private historical archive.

See `docs/REPOSITORY_TOPOLOGY.md` and `docs/SECURITY_MIGRATION_2026-09-24.md`.
