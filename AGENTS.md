# Climate Pulse agent instructions

These instructions are binding for GPT/Codex/other project agents working in
`zhongzq23-design/climate-pulse`.

## Repository authority

The public `climate-pulse` repository is the production source of truth for
processing code, tests, QC, generated data, website source and automation.

Before architecture-sensitive changes, read:

- `docs/REPOSITORY_TOPOLOGY.md`
- `lesson.md`
- `docs/PUBLIC_SCIENCE_WRITING_RULES.md` for public scientific copy

The private `climate-pulse-backend` and `climate-pulse-legacy-private`
repositories are rollback/history surfaces after cutover and must not run a
competing production schedule.

## Security invariants

- Never commit credentials, service-account JSON, API keys or tokens.
- PR workflows must be secret-free and read-only.
- Never use `pull_request_target` for repository code execution.
- Secret-dependent workflows may run only from trusted main/manual contexts.
- Production repository writes use the built-in `GITHUB_TOKEN` and only the
  minimum required `contents: write` permission.
- Do not reintroduce a cross-repository publisher token.
- Do not print whole environments, enable shell xtrace, or echo secret values.
- GitHub Pages must deploy the filtered `public-dist` artifact, not the whole repository.

## Operational and scientific rules

Recurring automation must remain idempotent and concurrency-safe.  Preserve the
project's source-first hazard semantics, exposure definitions, QC gates and
public-science wording rules.  Run the regression suite and
`scripts/check_public_science_copy.py` before release.
