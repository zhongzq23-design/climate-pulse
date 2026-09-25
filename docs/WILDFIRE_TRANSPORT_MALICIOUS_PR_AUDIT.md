# Simulated malicious-PR audit — wildfire public migration

Date: 2026-09-25

Threat model: an untrusted contributor forks the public repository, changes
Python/workflow files, and opens a pull request attempting to exfiltrate
Earth Engine or GitHub credentials.

## Simulated attacks and result

1. PR code prints environment variables: BLOCKED from secrets. PR workflows
   receive no Earth Engine secret; no pull_request_target trigger exists.
2. PR replaces the research script with a network exfiltration payload:
   BLOCKED from secrets for the same reason; research PR checks are unprivileged.
3. PR edits a secret-bearing workflow to checkout attacker code:
   BLOCKED pre-merge. The edited workflow does not gain protected environment
   secrets merely by being proposed in a pull request; main is branch-protected.
4. Dependency-tag substitution attack against checkout/setup-python/cache in
   secret-bearing workflows: MITIGATED by pinning those actions to full commit
   SHAs on this branch.
5. Accidental committed credential/API key/private key: BLOCKED by
   scripts/audit_public_secrets.py in the standard PR validation workflow.
6. Secret recovery from the frozen reference capsule: BLOCKED by capsule policy;
   credentials/private download URLs are prohibited and the capsule is verified
   by SHA-256 manifest before replay.
7. workflow_run privilege confusion into Pages: no secret exposure found. Pages
   checks out trusted main plus production-data and does not consume Earth
   Engine credentials.

Residual risk: a malicious change that is reviewed and merged into trusted main
could execute in a later scheduled/manual secret-bearing workflow. Branch
protection, code review, pinned actions, environment protection, and the
repository secret scanner are therefore all part of the security boundary.

Result: PASS for the simulated untrusted-PR exfiltration model. This is a
defense-in-depth assessment, not a claim of zero security risk.
