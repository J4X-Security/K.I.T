```text
K K  III  TTTTT
K K   I     T
KK    I     T
K K   I     T
K K  III    T

Known Issue Triager
```

# KIT / Known Issue Triager

KIT builds and checks a canonical `known-issues.json` register from audit reports.
It is designed for security review workflows where previous findings need to be
deduplicated and a new report needs to be compared against what is already known.

KIT supports:

- Claude Code through the `/known-issues` command
- Codex through the `$known-issues-aggregator` skill
- direct CLI use through the shared Python engine

## What It Does

- ingests local audit files, folders, repo directories, URLs, GitHub file URLs,
  GitHub folder URLs, and whole GitHub repo URLs
- downloads and normalizes remote sources, including PDFs
- stages report text for model-assisted issue extraction
- deduplicates extracted findings into one canonical `known-issues.json`
- prepares semantic duplicate checks for one issue or a whole report

`known-issues.json` is the only canonical output artifact.

## Requirements

- Python 3.11 or newer
- `pdfplumber`
- `pypdf`
- Claude Code and/or Codex if you want the host-integrated skill workflow

Install Python dependencies from this checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Install

### Claude Code

```bash
./scripts/install_claude_known_issues.sh
```

This installs:

- `~/.claude/known-issues-skill`
- `~/.claude/commands/known-issues.md`

Start a new Claude Code session and use:

```text
/known-issues
```

### Codex

```bash
./scripts/install_codex_known_issues.sh
```

This installs:

- `~/.codex/skills/known-issues-aggregator`

Start a new Codex session and invoke:

```text
$known-issues-aggregator
```

## Verify Installation

Claude Code:

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

## Host Workflow

Claude Code opens through `/known-issues`; Codex opens through
`$known-issues-aggregator`.

Both host workflows should guide you through:

- `build`, `check`, or `help`
- `extend` or `rebuild` when `known-issues.json` already exists
- iterative source collection until you reply `done`

The Python engine does not make model judgments. It prepares source text and
staged JSON contracts. The host model is responsible for extraction,
deduplication, and duplicate decisions according to the staged contract.

## Direct CLI

The shared engine lives at:

```text
claude-skill-known-issues/scripts/known_issues.py
```

Available commands:

- `prepare-build`
- `finalize-build`
- `prepare-check`

The Codex script is a wrapper around the same engine:

```text
codex-skill-known-issues/scripts/known_issues.py
```

## Build Workflow

Use the staged model-assisted flow when sources are messy, mixed-format,
PDF-based, URL-based, or likely to need semantic judgment.

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
5. Deduplicate the new findings and existing issues into `canonical_issues`.
6. Finalize:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --merge-known known-issues.json \
  --output known-issues.json
```

For a rebuild from scratch, omit `--merge-known` during prepare and finalize.

## Check Workflow

Prepare a staged duplicate check from a file:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-file /path/to/new-issue.md
```

Or from inline text:

```bash
python3 claude-skill-known-issues/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-text "Unchecked transfer result can desynchronize reward accounting."
```

This writes a staged JSON file containing:

- the full known register as `known_issues`
- the incoming report or issue as `report_text`
- an `llm_contract` with finding extraction rules, duplicate decision rules,
  and required output fields

The intended host flow is:

1. read and follow `llm_contract`
2. identify findings from `report_text`
3. evaluate one finding at a time against the full known register
4. when multiple findings are present and delegation is available, use one
   worker per finding and merge results by `finding_index`

## Development

Run the test suite with explicit discovery:

```bash
python3 -m unittest discover -s tests -q
```

The plain `python3 -m unittest -q` command does not discover this repo's tests
because the tests are under `tests/`.

## Limitations

- GitHub folder and repo URLs require network access to expand source files.
- PDF text extraction quality varies by report format.
- KIT intentionally fails when staged model output is missing instead of falling
  back to heuristic extraction or title-only matching.
- Duplicate decisions are semantic: root cause, affected surface, exploit path,
  and impact matter more than exact title similarity.

## Uninstall

Claude Code:

```bash
rm ~/.claude/known-issues-skill ~/.claude/commands/known-issues.md
```

Codex:

```bash
rm ~/.codex/skills/known-issues-aggregator
```

## License

MIT. See [LICENSE](LICENSE).
