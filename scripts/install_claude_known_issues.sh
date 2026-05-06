#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_SKILL_DIR="${REPO_ROOT}/claude-skill-known-issues"
CLAUDE_DIR="${HOME}/.claude"
INSTALLED_SKILL_PATH="${CLAUDE_DIR}/known-issues-skill"
COMMANDS_DIR="${CLAUDE_DIR}/commands"
COMMAND_PATH="${COMMANDS_DIR}/known-issues.md"
SOURCE_COMMAND_FILE="${REPO_ROOT}/claude-command-known-issues.md"

if [[ ! -d "${SOURCE_SKILL_DIR}" ]]; then
  echo "error: skill source directory not found: ${SOURCE_SKILL_DIR}" >&2
  exit 1
fi

if [[ ! -f "${SOURCE_COMMAND_FILE}" ]]; then
  echo "error: command source file not found: ${SOURCE_COMMAND_FILE}" >&2
  exit 1
fi

mkdir -p "${COMMANDS_DIR}"
ln -sfn "${SOURCE_SKILL_DIR}" "${INSTALLED_SKILL_PATH}"
cp "${SOURCE_COMMAND_FILE}" "${COMMAND_PATH}"

echo "Installed KIT / Known Issue Triager for Claude Code"
echo "Command: /known-issues"
echo "Skill path: ${INSTALLED_SKILL_PATH}"
echo "Command file: ${COMMAND_PATH}"
echo
echo "Verify with:"
echo "  ls -la \"${INSTALLED_SKILL_PATH}\""
echo "  sed -n '1,120p' \"${COMMAND_PATH}\""
