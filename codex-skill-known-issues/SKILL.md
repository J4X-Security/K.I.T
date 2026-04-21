---
name: known-issues-aggregator
description: Use when asked to consolidate audit findings into a canonical known-issues register, extend an existing known-issues file with new audit sources, or check whether a newly reported issue is already known. Supports local files, local audit folders, repo directories, direct URLs, GitHub file URLs, GitHub folder URLs, and whole GitHub repo URLs.
---

# Known Issues Aggregator

Use this skill when the task is to turn a collection of audit reports into a canonical known-issues register or to compare a new issue against that register.

## Start Rule

Do not start running commands immediately.

Always begin by asking the user what they want to do, even if the user invoked the skill directly. The first response should be a small conversational chooser, not execution.

Start by asking them to choose one of:

- build a known-issues register
- check a new issue against an existing register
- get help on the workflow

If they choose build, ask a second question before doing any work:

- extend the existing `known-issues.md`
- rebuild from scratch

If they choose build, collect sources iteratively instead of assuming them all at once. After each source is added, ask what to add next:

- local path
- URL
- done

Do not run the engine until the user has explicitly chosen a mode and, for build mode, explicitly finished source collection.

## Engine

This Codex skill uses the shared engine wrapper at:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py
```

The wrapper delegates to the shared engine from this repository and exposes the same commands:

- `prepare-build`
- `finalize-build`
- `build`
- `check`

## Build Workflow

Guide the user through:

1. Ask whether to extend an existing `known-issues.md` or rebuild from scratch.
2. Collect sources iteratively.
3. Sources may be:
   - a local report file
   - a local audit folder
   - a whole local repo directory
   - a direct report URL
   - a GitHub file URL
   - a GitHub folder URL
   - a whole GitHub repo URL
4. Prefer the staged flow:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py prepare-build \
  --input path/to/report-or-folder \
  --input https://github.com/org/audit-repo/tree/main/reports \
  --workspace-dir .known-issues-work
```

5. Read `.known-issues-work/prepared-build.json`.
6. For each prepared source, read the normalized text file path listed there.
7. Extract structured issue records into a JSON file with one result per source.
8. Finalize:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py finalize-build \
  --prepared .known-issues-work/prepared-build.json \
  --extractions codex-extractions.json \
  --output known-issues.md
```

To extend an existing register:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py finalize-build \
  --prepared .known-issues-work/prepared-build.json \
  --extractions codex-extractions.json \
  --merge-known known-issues.md \
  --output known-issues.md
```

## Check Workflow

Before running check mode, confirm whether the user is providing:

- inline issue text
- a local issue file

Then compare the new issue against the existing register using:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-text "Unchecked transfer result can desynchronize reward accounting."
```

Or:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-file path/to/new-issue.md
```

## Operating Rules

- The first step is always an explicit user choice flow. Do not jump directly into build or check execution.
- Prefer the staged flow for irregular formats, URLs, PDFs, GitHub repos, and GitHub folders.
- Treat `known-issues.md` as the human-facing artifact and `known-issues.json` as the machine-friendly sidecar.
- Preserve source traceability, aliases, source locations, and evidence snippets where available.
- Continue builds when a source is weak or partial, but surface warnings clearly.
- Use semantic duplicate matching rather than exact-title matching only.
