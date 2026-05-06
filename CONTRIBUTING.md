# Contributing

KIT is a small staged workflow for building and checking `known-issues.json`
registers from audit reports. Contributions should keep that workflow explicit:
the Python engine prepares source text and JSON contracts, while the host model
performs extraction, deduplication, and duplicate review.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Tests

Run tests with explicit discovery:

```bash
python3 -m unittest discover -s tests -q
```

Add focused tests for behavior changes in the CLI, staged JSON contracts,
installer scripts, and host metadata. Avoid tests that only assert long prompt
copy unless the exact copy is part of the public contract.

## Development Notes

- Keep `known-issues.json` as the only canonical register artifact.
- Keep build/check behavior staged through `prepare-build`, `finalize-build`,
  and `prepare-check`.
- Do not add deterministic fallback extraction or title-only duplicate matching.
  Missing model-authored staged data should fail loudly.
- Keep Claude Code and Codex instructions aligned when changing workflow rules.
- Keep install scripts portable and based on `$HOME` plus repo-relative paths.

## Release Checklist

- Run `python3 -m unittest discover -s tests -q`.
- Check `python3 claude-skill-known-issues/scripts/known_issues.py --help`.
- Check `python3 codex-skill-known-issues/scripts/known_issues.py --help`.
- Verify README install and uninstall commands match the scripts.
- Confirm public-facing docs use KIT / Known Issue Triager naming.
