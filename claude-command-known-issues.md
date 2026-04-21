---
description: "Build or check a known-issues register from audit reports"
argument-hint: [build | check | help]
allowed-tools: AskUserQuestion, Read, Write, Bash(python3:*)
---

Read `~/.claude/known-issues-skill/SKILL.md` and follow its instructions exactly.

Use `python3 ~/.claude/known-issues-skill/scripts/known_issues.py` whenever the task requires generating or checking known issues.

The user invoked this command with: `$ARGUMENTS`

## Interaction Model

If `$ARGUMENTS` is empty or only whitespace:

1. Use `AskUserQuestion` to present a small mode selector.
2. Ask:
   - header: `Mode`
   - question: ` _  _____ ___
| |/ /_ _/ __|
| ' < | | (__
|_|\_\___\___|
KIC (Known Issue Checker) by J4X

What do you want to do with the known issues workflow?`
   - options:
     - `Build register` — Parse audit reports and generate `known-issues.json`
     - `Check issue` — Compare a new issue against existing known issues
     - `Help` — Show usage examples and expected inputs
3. Then continue based on the selected mode.

If the mode is `Check issue`, ask a second question:
- header: `Issue input`
- question: `How are you providing the new issue to compare?`
- options:
  - `Inline text` — The issue will be pasted directly in chat
  - `Issue file` — The issue exists in a local file
  - `Need help` — Show the expected formats and examples

## Build Register Flow

When building the register:

- At the start of build mode, if `known-issues.json` already exists in the current working directory, use `AskUserQuestion` to ask:
  - header: `Build mode`
  - question: `Do you want to extend the existing known-issues register or rebuild from scratch?`
  - options:
    - `Extend existing` — Merge new source findings into the current known-issues register and deduplicate again
    - `Rebuild` — Ignore the current register and rebuild only from the sources provided in this run
- If no existing `known-issues.json` exists, default to rebuild behavior without asking.
- Collect any report sources already present in the command arguments and conversation.
- Then enter a source collection loop using `AskUserQuestion`.
- In each loop iteration, ask:
  - header: `Add source`
  - question: `What do you want to add next?`
  - options:
    - `Local path` — Add one local report file path or a local directory containing audits
    - `URL` — Add one report URL, GitHub folder URL, or whole GitHub repo URL
    - `Done` — Stop adding sources and continue
- If the user selects `Local path`, ask them for exactly one path in their next message, add it to the source list, then return to the same loop.
- Local paths may be:
  - a single report file
  - a whole local subfolder
  - a whole local repo directory
- If the path is a directory, the build step should recursively collect supported audit-like files from it.
- If the user selects `URL`, ask them for exactly one URL in their next message, add it to the source list, then return to the same loop.
- URLs may be:
  - a single report URL
  - a GitHub file URL
  - a GitHub repo subfolder URL
  - a whole GitHub repo URL
- GitHub repo and folder URLs should expand to supported audit-like files within that container before preparation.
- If the user selects `Done`, exit the loop.
- Do not continue to script execution until the user has either provided at least one source or explicitly confirmed they want to stop and revise.
- If the user exits the loop with zero sources, explain that at least one source is required and offer to restart the source loop.
- Once the source list is complete, run:
  - `prepare-build` to download and normalize all sources into a single `known-issues.json` state file
  - if the user chose `Extend existing`, pass `--merge-known known-issues.json` to `prepare-build` so the current register is snapshotted into the state file
  - read the generated `known-issues.json`
  - if `existing_issues_snapshot` is present, treat it as the current canonical register
  - read each normalized source text file listed there
  - use Claude to fill `source_results` in that same `known-issues.json`
  - use Claude to deduplicate both the new extracted findings and any issues in `existing_issues_snapshot`, then write the final canonical register into `canonical_issues` in that same `known-issues.json`
  - `finalize-build` to merge those Claude extraction results into `known-issues.json`
- If the user chose `Extend existing`, pass `--merge-known known-issues.json` to `finalize-build`.
- If the user chose `Rebuild`, do not pass `--merge-known`.
- The single JSON state file should contain:
  - top-level `source_results`
  - each result contains `source_id`, `status`, `warnings`, and `issues`
  - each issue contains `title`, `summary`, `root_cause`, `impact`, `affected_component`, `severity`, `aliases`, `source_location`, `evidence_snippet`, and `extraction_confidence`
- Default output path: `known-issues.json` in the current working directory unless the user specified another path.
- Report:
  - source count
  - canonical issue count
  - output file paths
  - any failed sources or weak extractions

## Check Issue Flow

When checking a new issue:

- Look for `known-issues.json` in the current working directory unless the user specified a different path.
- Prefer staged check mode:
  - run `prepare-check`
  - read the staged JSON
  - read `llm_contract` and follow it exactly
  - identify findings from `report_text`
  - for each identified finding, do one Claude judgment against the full `known_issues` list using the `duplicate_check` contract
  - if delegation is available and there are multiple findings, spawn one worker per finding and pass each worker the finding, its 1-based index, the full `known_issues` list, and the `duplicate_check` contract
  - merge worker outputs into one ordered result list sorted by `finding_index`
  - return one verdict per finding using the required output schema
- If staged LLM output is missing or incomplete, fail instead of guessing.

## Help Flow

If the user selects `Help`, explain:

- `/known-issues` opens the chooser
- `/known-issues` uses staged JSON workflows for both build and check
- report files can be local files and report sources can also be URLs
- the build flow downloads remote artifacts and extracts PDF text before Claude structures the issues

## Direct Arguments

If `$ARGUMENTS` already clearly starts with `build`, `check`, or `help`, skip the first interactive question and execute the corresponding flow directly.
