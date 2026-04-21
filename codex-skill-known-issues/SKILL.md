---
name: known-issues-aggregator
description: Use when asked to consolidate audit findings into a canonical known-issues.json register, extend an existing known-issues file with new audit sources, or check whether a newly reported issue is already known. Supports local files, local audit folders, repo directories, direct URLs, GitHub file URLs, GitHub folder URLs, and whole GitHub repo URLs.
---

# Known Issues Aggregator

Use this skill when the task is to turn a collection of audit reports into a canonical known-issues register or to compare a new issue against that register.

## Start Rule

Do not start running commands immediately.

Always begin by asking the user what they want to do, even if the user invoked the skill directly. The first response should be a small conversational chooser, not execution.

Do not repeat that the skill is being used. The host already shows that.

Do not render the opening menu twice.

The first response must contain exactly one question block, then stop and wait for the user's reply.

Use this exact opening format:

```text
 _  _____ ___
| |/ /_ _/ __|
| ' < | | (__
|_|\_\___\___|
KIC (Known Issue Checker) by J4X

What do you want to do?
- build: create or extend a known-issues.json register from audit sources
- check: compare one new issue against an existing register
- help: show the workflow and examples

Reply with: build, check, or help.
```

If they choose build and `known-issues.json` already exists in the current working directory, ask a second question before doing any work:

`Reply with: extend or rebuild.`

If `known-issues.json` does not exist yet, do not ask this question. Default to rebuild behavior.

If they choose build, collect sources iteratively instead of assuming them all at once.

Do not ask the user to classify a source as local or URL first. Infer that from the string they provide.

After each source is added, ask for the next source in one short line, for example:

`Send the next source path or URL, or reply done.`

Infer source type from the value:

- local file path
- local folder path
- local repo directory
- direct URL
- GitHub file URL
- GitHub folder URL
- whole GitHub repo URL

Do not run the engine until the user has explicitly chosen a mode and, for build mode, explicitly finished source collection.

## Engine

This Codex skill uses the shared engine wrapper at:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py
```

The wrapper delegates to the shared engine from this repository and exposes the same commands:

- `prepare-build`
- `finalize-build`
- `prepare-check`

## Build Workflow

Guide the user through:

1. If `known-issues.json` already exists in the current working directory, ask whether to extend it or rebuild from scratch. Otherwise default to rebuild without asking.
2. Collect sources iteratively.
3. Sources may be:
   - a local report file
   - a local audit folder
   - a whole local repo directory
   - a direct report URL
   - a GitHub file URL
   - a GitHub folder URL
   - a whole GitHub repo URL
4. Infer each provided source from its string value rather than asking the user to label it.
5. Prefer the staged flow:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py prepare-build \
  --input path/to/report-or-folder \
  --input https://github.com/org/audit-repo/tree/main/reports \
  --merge-known known-issues.json \
  --state-file known-issues.json
```

6. Read `known-issues.json`.
7. If `existing_issues_snapshot` is present, treat those as the current canonical register for extend mode.
8. For each prepared source, read the normalized text file path listed there.
9. Fill `source_results` inside that same `known-issues.json`.
10. Deduplicate both the new extracted issues and the existing canonical issues from `existing_issues_snapshot` when present, then write the final canonical register into `canonical_issues` inside that same `known-issues.json`.
11. Finalize:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --output known-issues.json
```

To extend an existing register:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py finalize-build \
  --state-file known-issues.json \
  --merge-known known-issues.json \
  --output known-issues.json
```

## Check Workflow

Before running check mode, confirm whether the user is providing:

`Reply with: text or file.`

Prefer the staged check flow:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-file path/to/new-issue.md
```

Or:

```bash
python3 ~/.codex/skills/known-issues-aggregator/scripts/known_issues.py prepare-check \
  --known known-issues.json \
  --issue-text "Unchecked transfer result can desynchronize reward accounting."
```

Then:

1. Read the staged JSON output.
2. Read `llm_contract` and follow it exactly.
3. Read `report_text` and identify the findings from that raw report text using the `finding_extraction` contract.
4. If there is more than one finding, spawn one subagent per finding so the duplicate checks run in parallel.
5. Give each subagent:
   - exactly one finding you identified from `report_text`
   - the full `known_issues` list
   - the `duplicate_check` contract from `llm_contract`
   - the finding's 1-based index
   - a narrow task: return a result that matches the required output schema exactly
6. If there is exactly one finding, do the judgment in the main thread using the same contract.
7. After all subagents finish, merge their outputs into one ordered result list sorted by `finding_index`.
8. Return one verdict per finding using the required output schema from `llm_contract`.

## Operating Rules

- The first step is always an explicit user choice flow. Do not jump directly into build or check execution.
- Ask each choice question once per step, then wait for the user's reply. Do not repeat the same question in the same turn.
- Keep all choice prompts compact and single-line. Do not print large bullet menus unless the user asks for help.
- During source collection, ask for the next source value directly. Do not ask the user to classify it as local or URL first.
- Prefer the staged flow for irregular formats, URLs, PDFs, GitHub repos, and GitHub folders.
- In the staged flow, the model is responsible for deduping extracted findings and, in extend mode, deduping them against `existing_issues_snapshot` before writing the final canonical issues into `canonical_issues`.
- In check mode, prefer `prepare-check`, identify findings from `report_text`, and then make one model judgment per finding against the full known register.
- In check mode, the duplicate decision must follow the staged `llm_contract`, not ad hoc judgment.
- When multiple findings are present, spawning one subagent per finding is required.
- Do not use deterministic fallback for build or check. If staged LLM output is missing, fail instead of guessing.
- Treat `known-issues.json` as the only canonical artifact.
- During staged builds, reuse the same `known-issues.json` file instead of creating separate preview or extraction JSON files.
- Preserve source traceability, aliases, source locations, and evidence snippets where available.
- Continue builds when a source is weak or partial, but surface warnings clearly.
- Use semantic duplicate matching rather than exact-title matching only.
