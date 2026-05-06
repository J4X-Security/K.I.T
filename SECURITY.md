# Security Policy

KIT is intended for audit and vulnerability triage workflows. Inputs may contain
sensitive report text, private source locations, or unreleased findings.

## Reporting Vulnerabilities

If you find a vulnerability in KIT itself, open a private report through the
repository's GitHub security advisory flow when available. If advisories are not
enabled, contact the repository maintainer through the public profile listed on
GitHub and avoid posting exploit details in a public issue.

## Handling Sensitive Inputs

- Do not commit private audit reports, normalized source text, downloaded PDFs,
  staged workspaces, or generated `known-issues.json` files unless they are
  intended to be public.
- Review staged JSON before sharing it. It includes raw `report_text`, normalized
  source paths, and known issue details.
- Remote URL and GitHub source expansion requires network access and may disclose
  requested URLs to remote services.
