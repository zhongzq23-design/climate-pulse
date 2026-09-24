# Public migration security audit — 2026-09-24

## Scope

Pre-cutover audit of `zhongzq23-design/climate-pulse-backend` and migration
into public `zhongzq23-design/climate-pulse`.

## Findings

- Full reachable commit/branch/PR credential-history scan: no real PAT, API key,
  bearer token, Google/AWS key, PEM/private key, or plaintext secret assignment found.
- Active and retained workflow/script logging scan: 54 files checked; no
  `print(os.environ)`, `printenv`, full `env` dump, shell xtrace, PowerShell
  environment dump, or direct secret-value echo found.
- No `pull_request_target` workflow trigger found.
- A harmless diagnostic that prints only the name `EARTHDATA_TOKEN` when the
  value is absent was reviewed and does not expose the value.
- Cross-repository `PUBLIC_REPO_TOKEN` usage is not carried into the public
  monorepo production path.

## Cutover controls

- PR validation: read-only and secret-free.
- Secret-dependent Earth Engine/Earthdata jobs: trusted main/manual contexts only.
- Same-repository production commits: built-in `GITHUB_TOKEN`.
- GitHub Pages: filtered `public-dist` artifact.
- Regression test: `tests/test_workflow_security.py` prevents reintroduction of
  the main credential/workflow hazards above.
