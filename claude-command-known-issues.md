---
description: "Build or check a known-issues register from audit reports"
argument-hint: [build | check | help]
allowed-tools: AskUserQuestion, Read, Bash(python3:*)
---

Read `~/.claude/known-issues-skill/SKILL.md` and follow its instructions exactly.

Use `python3 ~/.claude/known-issues-skill/scripts/known_issues.py` whenever the task requires generating or checking known issues.

The user invoked this command with: `$ARGUMENTS`

## Interaction Model

If `$ARGUMENTS` is empty or only whitespace:

1. Use `AskUserQuestion` to present a small mode selector.
2. Ask:
   - header: `Mode`
   - question: `What do you want to do with the known issues workflow?`
   - options:
     - `Build register` — Parse audit reports and generate `known-issues.md`
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

- Collect any report sources already present in the command arguments and conversation.
- Then enter a source collection loop using `AskUserQuestion`.
- In each loop iteration, ask:
  - header: `Add source`
  - question: `What do you want to add next?`
  - options:
    - `Local path` — Add one local report file path
    - `URL` — Add one report URL
    - `Done` — Stop adding sources and continue
- If the user selects `Local path`, ask them for exactly one path in their next message, add it to the source list, then return to the same loop.
- If the user selects `URL`, ask them for exactly one URL in their next message, add it to the source list, then return to the same loop.
- If the user selects `Done`, exit the loop.
- Do not continue to script execution until the user has either provided at least one source or explicitly confirmed they want to stop and revise.
- If the user exits the loop with zero sources, explain that at least one source is required and offer to restart the source loop.
- Once the source list is complete, run the helper script in `build` mode.
- Default output path: `known-issues.md` in the current working directory unless the user specified another path.
- Report:
  - source count
  - canonical issue count
  - output file paths
  - any failed sources or weak extractions

## Check Issue Flow

When checking a new issue:

- Look for `known-issues.md` in the current working directory unless the user specified a different path.
- If a new issue file is provided, run the helper script with `check --issue-file`.
- If inline issue text is provided, run the helper script with `check --issue-text`.
- Return the helper result in a concise human-readable form:
  - verdict
  - confidence
  - closest known issue, if any
  - rationale

## Help Flow

If the user selects `Help`, explain:

- `/known-issues` opens the chooser
- `/known-issues build ...` can be used directly for scripted flows
- `/known-issues check ...` can be used directly for duplicate checks
- report files can be local files and report sources can also be URLs

## Direct Arguments

If `$ARGUMENTS` already clearly starts with `build`, `check`, or `help`, skip the first interactive question and execute the corresponding flow directly.
