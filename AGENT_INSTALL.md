# KIT Agent-Assisted Install Prompt

Paste this whole prompt into Claude Code or Codex to install KIT / Known Issue
Triager on the current machine.

```text
Install KIT / Known Issue Triager for this agent host.

Use the existing checkout if the current working directory is already the
K.I.T. repository. Otherwise clone the public repository first:

  git clone https://github.com/J4X-Security/K.I.T..git ~/K.I.T

Then work from the repository root.

Set up the Python environment:

  python3 -m venv .venv
  . .venv/bin/activate
  pip install -r requirements.txt

Install the host integration:

- If this is Claude Code, run:

    bash scripts/install_claude_kit.sh

- If this is Codex, run:

    bash scripts/install_codex_kit.sh

- If the host is unclear, ask me once whether to install for Claude Code or
  Codex, then run only that installer.

Verify the install:

- For Claude Code:

    ls -la ~/.claude/kit-skill
    sed -n '1,120p' ~/.claude/commands/kit.md
    python3 ~/.claude/kit-skill/scripts/known_issues.py --help

- For Codex:

    ls -la ~/.codex/skills/kit
    sed -n '1,120p' ~/.codex/skills/kit/SKILL.md
    python3 ~/.codex/skills/kit/scripts/known_issues.py --help

When finished, tell me which command to use next:

- Claude Code: /kit
- Codex: $kit
```
