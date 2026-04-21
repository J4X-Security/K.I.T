# Known Issues Aggregator

Build and maintain a single `known-issues.json` register from audit reports, then check whether a newly reported issue is already known.

This repo supports:

- Claude Code
- Codex
- direct CLI use

## What It Does

- ingests local audit files, folders, repo directories, URLs, GitHub file URLs, GitHub folder URLs, and whole GitHub repo URLs
- downloads and normalizes remote sources, including PDFs
- extracts issue candidates from each source
- deduplicates them into one canonical `known-issues.json`
- checks whether a new issue matches an existing known issue

The canonical output is:

- `known-issues.json`

## Install

### Claude Code

```bash
./scripts/install_claude_known_issues.sh
```

This installs:

- `~/.claude/known-issues-skill`
- `~/.claude/commands/known-issues.md`

After that, start a new Claude session and use:

```text
/known-issues
```

### Codex

```bash
./scripts/install_codex_known_issues.sh
```

This installs:

- `~/.codex/skills/known-issues-aggregator`

After that, start a new Codex session and invoke:

```text
$known-issues-aggregator
```

## Verify

Claude:

```bash
ls -la ~/.claude/known-issues-skill
sed -n '1,120p' ~/.claude/commands/known-issues.md
python3 ~/.claude/known-issues-skill/scripts/known_issues.py --help
```

Codex:

```bash
ls -la ~/.codex/skills/known-issues-aggregator
sed -n '1,120p' ~/.codex/skills/known-issues-aggregator/SKILL.md
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py --help
```

## Host Behavior

Claude opens through `/known-issues` and uses a small interactive chooser.

Codex starts through `$known-issues-aggregator` and should guide you through:

- `build`, `check`, or `help`
- `extend` or `rebuild` when `known-issues.json` already exists
- iterative source collection until you reply `done`

## Direct CLI

The shared engine lives at:

```text
claude-skill-known-issues/scripts/known_issues.py
```

Available commands:

- `prepare-build`
- `finalize-build`
- `prepare-check`

## Build Workflow

Use the staged model-assisted flow to build the register.

Use this when sources are messy, mixed-format, PDF-based, or likely to need model judgment.

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-build \
  --input /path/to/report.md \
  --input /path/to/audits-folder \
  --input https://example.com/report.pdf \
  --merge-known known-issues.json \
  --state-file known-issues.json
```

Then:

1. Read `known-issues.json`.
2. Read each normalized source text file listed in `sources`.
3. Write per-source findings into `source_results`.
4. If `existing_issues_snapshot` is present, treat it as the current register.
5. Deduplicate both the new findings and the existing issues into `canonical_issues`.
6. Finalize:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --merge-known known-issues.json \
  --output known-issues.json
```

## Check Workflow

Recommended staged model-assisted check:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-file /path/to/new-issue.md
```

Or:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-text "Unchecked transfer result can desynchronize reward accounting."
```

This writes a staged JSON file containing:

- the full known register as `known_issues`
- the parsed incoming findings as `findings`

The intended host flow is to evaluate one finding at a time against the full known register.

When multiple findings are present and the host supports delegation, the intended pattern is one subagent per finding so those duplicate checks can run in parallel.

## Notes

- local folders and repo directories are expanded recursively into supported audit-like files
- GitHub folder and repo URLs are expanded before download
- PDFs are downloaded locally and normalized before extraction
- `known-issues.json` is the only canonical artifact
- extend mode is intended to deduplicate new issues against the existing register, not just within the new sources
