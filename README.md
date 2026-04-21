# Claude Skill: Known Issues Aggregator

This repository contains a Claude skill that consolidates audit findings into a canonical known-issues register.

It supports two workflows:

- build `known-issues.md` from a set of audit reports provided as local file paths or URLs
- check whether a newly reported issue is already covered by the existing known-issues register

The skill package lives in [`claude-skill-known-issues/`](./claude-skill-known-issues).

The recommended extraction mode is Claude-first: the script downloads and normalizes sources, including PDFs, and Claude structures issue records from that prepared text before final deduplication.

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

For Claude Code, the easiest setup is to install a global slash command that points at this repository's skill files.

Run:

```bash
./scripts/install_claude_known_issues.sh
```

This script:

- creates `~/.claude/known-issues-skill` as a symlink to `claude-skill-known-issues/`
- creates `~/.claude/commands/known-issues.md`
- makes the skill available in future Claude Code sessions via `/known-issues`
- installs an interactive `/known-issues` command that opens with a small mode chooser when run without arguments

After that, you can use:

```text
/known-issues build known issues from @report1.md and @report2.md
```

```text
/known-issues check whether this issue is already known: unchecked transfer result can desynchronize reward accounting
```

If you run just:

```text
/known-issues
```

the command opens with a small interactive selector so the user can choose whether to:

- build a known-issues register
- check a new issue
- view usage help

In build mode, source collection is iterative: the command keeps offering to add one local path or one URL at a time, and the user can exit the loop with `Done` when finished.

### Manual Installation

Claude installation conventions vary by environment. If you prefer to install things yourself, these are the equivalent manual steps.

#### Option 1: Symlink for development

Use a symlink if you want edits in this repository to be reflected immediately in the installed skill.

```bash
ln -s "$(pwd)/claude-skill-known-issues" ~/.claude/known-issues-skill
```

Create a global slash command:

```bash
mkdir -p ~/.claude/commands
```

Create `~/.claude/commands/known-issues.md` with:

```md
---
description: "Build or check a known-issues register from audit reports"
argument-hint: [build | check | help]
allowed-tools: AskUserQuestion, Read, Bash(python3:*)
---

Read ~/.claude/known-issues-skill/SKILL.md and follow its instructions exactly.

If the task requires generating or checking known issues, use:
`python3 ~/.claude/known-issues-skill/scripts/known_issues.py`

Arguments: $ARGUMENTS
```

For the full interactive version, copy the repository file `claude-command-known-issues.md` into `~/.claude/commands/known-issues.md`.

#### Option 2: Copy for a standalone install

Use a copy if you want the installed skill to remain independent from this repository.

```bash
cp -R claude-skill-known-issues ~/.claude/known-issues-skill
```

Then create the same `~/.claude/commands/known-issues.md` file shown above.

## Verify The Install

After installation, confirm the skill directory contains:

- `SKILL.md`
- `scripts/known_issues.py`

You can also verify both the command and the helper script:

```bash
ls -la ~/.claude/known-issues-skill
sed -n '1,120p' ~/.claude/commands/known-issues.md
python3 ~/.claude/known-issues-skill/scripts/known_issues.py --help
```

## Usage

You can invoke the helper directly from this repository or from the installed skill location.

### Build a canonical known issues file

Recommended Claude-first flow:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-build \
  --input /path/to/report-1.md \
  --input https://example.com/report-2.pdf \
  --workspace-dir .known-issues-work
```

Then read `.known-issues-work/prepared-build.json`, extract issue records with Claude from the normalized source text files, write them to a JSON file, and finalize:

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

Direct fallback flow:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py build \
  --input /path/to/report-1.md \
  --input https://example.com/report-2 \
  --merge-known known-issues.md \
  --output known-issues.md
```

This writes:

- `known-issues.md`
- `known-issues.json`

In Claude-first mode, `known-issues.json` also contains per-source extraction metadata, warnings, and evidence provenance.

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
- URLs and PDFs are downloaded locally before normalization.
- PDF text extraction is implemented through Python PDF libraries, then Claude is expected to structure issues from the normalized text.
- Duplicate detection is heuristic and semantic, not exact-title-only.
