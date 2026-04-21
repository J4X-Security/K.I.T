---
name: "known-issues-aggregator"
description: "Use this skill when asked to ingest audit reports from local files or URLs, deduplicate findings into a canonical known-issues.json register, or check whether a newly reported issue is already known."
---

# Known Issues Aggregator

Use this skill when the task is to consolidate prior audit findings into a single known-issues register or to decide whether a new issue is already covered by that register.

## What This Skill Produces

- `known-issues.json`: canonical known-issues register used for both builds and duplicate checks

## When To Use The Script

Use the helper script whenever the task includes any of:

- multiple audit report files
- report URLs
- local audit directories or whole local repos
- GitHub repo URLs or GitHub folder URLs that contain audits
- regenerating `known-issues.json`
- checking a new issue against an existing known-issues file

Prefer the staged workflow over manual synthesis because it downloads remote artifacts, extracts text from PDFs, and keeps the final JSON register aligned with the prepared source state.

## Build Known Issues

For Claude-first extraction, use the staged workflow:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-build \
  --input path/to/report-1.md \
  --input path/to/audits-folder \
  --input https://github.com/org/audit-repo/tree/main/reports \
  --merge-known known-issues.json \
  --state-file known-issues.json
```

Then:

1. Read `known-issues.json`
2. If `existing_issues_snapshot` is present, treat those as the current canonical register for extend mode
3. For each prepared source, read the normalized text file path listed there
4. Use Claude to fill `source_results` inside that same `known-issues.json`
5. Then use Claude to deduplicate both:
   - the new extracted issues from `source_results`
   - the existing canonical issues from `existing_issues_snapshot` when present
   into the final `canonical_issues` list inside that same `known-issues.json`
6. Finalize:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --output known-issues.json
```

To extend an existing register instead of rebuilding from scratch:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --merge-known known-issues.json \
  --output known-issues.json
```

Direct `build` still exists for deterministic fallback or scripted use:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py build \
  --input path/to/report-1.md \
  --input https://example.com/report-2 \
  --merge-known known-issues.json \
  --output known-issues.json
```

Behavior:

- accepts repeated `--input` values for local paths and HTTP(S) URLs
- local directory inputs are expanded recursively into supported audit-like files
- GitHub repo and folder URLs are expanded into supported audit-like files before download
- downloads remote artifacts locally
- extracts PDF text before issue extraction
- supports Claude-first extraction through `prepare-build` and `finalize-build`
- uses a single reusable `known-issues.json` state file during the staged flow
- expects Claude to author the final `canonical_issues` list during staged builds, including deduping against `existing_issues_snapshot` during extend mode
- can either rebuild from scratch or extend an existing `known-issues.json`
- writes `known-issues.json`

## Check A New Issue

Use one of:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.json \
  --issue-file path/to/new-issue.md
```

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.json \
  --issue-text "Unchecked return value in reward distributor can leave accounting inconsistent after external transfer failure."
```

Behavior:

- returns `known`, `possibly-known`, or `new`
- explains the closest match and the reasoning

## Operating Rules

- Treat `known-issues.json` as the only canonical artifact.
- Prefer Claude-assisted extraction for URLs, PDFs, GitHub-hosted reports, and irregular formats.
- Prefer Claude-assisted dedupe during staged builds: the model should decide which extracted issues collapse into one canonical issue and write that decision into `canonical_issues`.
- Collapse issues when the underlying root cause, affected surface, and impact are materially the same even if wording differs.
- Keep issues separate when they only share a component or severity but differ in bug class or exploit path.
- If extraction quality is weak for a source, record a warning instead of inventing structured findings.

## Expected Canonical Issue Fields

Each canonical issue should preserve:

- title
- summary
- root cause
- impact
- affected component
- aliases from source reports
- source report references
- source locations such as page or section when available
- evidence snippets when available

## Failure Handling

- If a URL cannot be fetched, surface the source and the fetch error.
- If a report yields weak or no structured candidates, keep going with the remaining sources and record that source as `partial` or `failed`.
- If a duplicate decision is borderline, return `possibly-known` and explain the ambiguity instead of forcing a collapse.
