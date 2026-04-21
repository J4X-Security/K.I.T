---
name: "known-issues-aggregator"
description: "Use this skill when asked to ingest audit reports from local files or URLs, deduplicate findings into a canonical known-issues.md, or check whether a newly reported issue is already known."
---

# Known Issues Aggregator

Use this skill when the task is to consolidate prior audit findings into a single known-issues register or to decide whether a new issue is already covered by that register.

## What This Skill Produces

- `known-issues.md`: human-readable canonical issue register
- `known-issues.json`: machine-friendly sidecar used for reliable follow-up duplicate checks

## When To Use The Script

Use the helper script whenever the task includes any of:

- multiple audit report files
- report URLs
- regenerating `known-issues.md`
- checking a new issue against an existing known-issues file

Prefer the staged workflow over manual synthesis because it downloads remote artifacts, extracts text from PDFs, and keeps the markdown and JSON outputs aligned.

## Build Known Issues

For Claude-first extraction, use the staged workflow:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-build \
  --input path/to/report-1.md \
  --input https://example.com/report-2 \
  --workspace-dir .known-issues-work
```

Then:

1. Read `.known-issues-work/prepared-build.json`
2. For each prepared source, read the normalized text file path listed there
3. Use Claude to extract structured issue records into a JSON file with one result per source
4. Finalize:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --prepared .known-issues-work/prepared-build.json \
  --extractions claude-extractions.json \
  --output known-issues.md
```

To extend an existing register instead of rebuilding from scratch:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --prepared .known-issues-work/prepared-build.json \
  --extractions claude-extractions.json \
  --merge-known known-issues.md \
  --output known-issues.md
```

Direct `build` still exists for deterministic fallback or scripted use:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py build \
  --input path/to/report-1.md \
  --input https://example.com/report-2 \
  --merge-known known-issues.md \
  --output known-issues.md
```

Behavior:

- accepts repeated `--input` values for local paths and HTTP(S) URLs
- downloads remote artifacts locally
- extracts PDF text before issue extraction
- supports Claude-first extraction through `prepare-build` and `finalize-build`
- can either rebuild from scratch or extend an existing `known-issues.md`
- writes `known-issues.md`
- writes `known-issues.json` beside it

## Check A New Issue

Use one of:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-file path/to/new-issue.md
```

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-text "Unchecked return value in reward distributor can leave accounting inconsistent after external transfer failure."
```

Behavior:

- loads `known-issues.json` automatically when it exists next to `known-issues.md`
- falls back to parsing `known-issues.md` directly if the sidecar is missing
- returns `known`, `possibly-known`, or `new`
- explains the closest match and the reasoning

## Operating Rules

- Treat `known-issues.md` as the human-facing artifact and the JSON sidecar as the matching index.
- Prefer Claude-assisted extraction for URLs, PDFs, GitHub-hosted reports, and irregular formats.
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
