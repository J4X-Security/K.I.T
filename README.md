# Claude Skill: Known Issues Aggregator

This repository contains a Claude skill that consolidates audit findings into a canonical known-issues register.

It supports two workflows:

- build `known-issues.md` from a set of audit reports provided as local file paths or URLs
- check whether a newly reported issue is already covered by the existing known-issues register

The skill package lives in [`claude-skill-known-issues/`](./claude-skill-known-issues).

## What It Produces

- `known-issues.md`: human-readable canonical issue register
- `known-issues.json`: machine-friendly sidecar used for more reliable duplicate checks

## Repository Layout

```text
claude-skill-known-issues/
  SKILL.md
  scripts/
    known_issues.py
tests/
```

## Installation

Claude skill installation varies by environment. If your Claude setup supports a local skills directory, install the contents of `claude-skill-known-issues/` into that directory under the name you want to use.

Two common approaches are:

### Option 1: Symlink for development

Use a symlink if you want edits in this repository to be reflected immediately in the installed skill.

```bash
mkdir -p <your-claude-skills-dir>
ln -s "$(pwd)/claude-skill-known-issues" \
  "<your-claude-skills-dir>/known-issues-aggregator"
```

### Option 2: Copy for a standalone install

Use a copy if you want the installed skill to remain independent from this repository.

```bash
mkdir -p <your-claude-skills-dir>
cp -R claude-skill-known-issues \
  "<your-claude-skills-dir>/known-issues-aggregator"
```

If your Claude environment expects a specific skill path or category structure, adapt the destination directory accordingly.

## Verify The InstallOk 

After installation, confirm the skill directory contains:

- `SKILL.md`
- `scripts/known_issues.py`

You can also verify that the helper script runs:

```bash
python3 <your-claude-skills-dir>/known-issues-aggregator/scripts/known_issues.py --help
```

## Usage

You can invoke the helper directly from this repository or from the installed skill location.

### Build a canonical known issues file

```bash
python3 claude-skill-known-issues/scripts/known_issues.py build \
  --input /path/to/report-1.md \
  --input https://example.com/report-2 \
  --output known-issues.md
```

This writes:

- `known-issues.md`
- `known-issues.json`

### Check whether a new issue is already known

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-text "Unchecked transfer result can desynchronize reward accounting."
```

Or:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py check \
  --known known-issues.md \
  --issue-file /path/to/new-issue.md
```

The command returns JSON containing:

- `verdict`
- `confidence`
- `matched_issue` when a close match exists
- `rationale`

## Notes

- v1 is strongest on text-like Markdown, HTML, and JSON reports.
- PDF parsing is not implemented.
- Duplicate detection is heuristic and semantic, not exact-title-only.
