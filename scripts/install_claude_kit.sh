#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_SKILL_DIR="${REPO_ROOT}/claude-skill-kit"
CLAUDE_DIR="${HOME}/.claude"
INSTALLED_SKILL_PATH="${CLAUDE_DIR}/kit-skill"
OLD_INSTALLED_SKILL_PATH="${CLAUDE_DIR}/known-issues-skill"
COMMANDS_DIR="${CLAUDE_DIR}/commands"
COMMAND_PATH="${COMMANDS_DIR}/kit.md"
OLD_COMMAND_PATH="${COMMANDS_DIR}/known-issues.md"
SOURCE_COMMAND_FILE="${REPO_ROOT}/claude-command-kit.md"

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

if [[ -L "${OLD_INSTALLED_SKILL_PATH}" ]]; then
  rm "${OLD_INSTALLED_SKILL_PATH}"
  echo "Removed old Claude skill path: ${OLD_INSTALLED_SKILL_PATH}"
fi

if [[ -f "${OLD_COMMAND_PATH}" ]] && grep -q "KIT / Known Issue Triager\\|known-issues-skill" "${OLD_COMMAND_PATH}"; then
  rm "${OLD_COMMAND_PATH}"
  echo "Removed old Claude command file: ${OLD_COMMAND_PATH}"
fi

echo "Installed KIT / Known Issue Triager for Claude Code"
echo "Command: /kit"
echo "Skill path: ${INSTALLED_SKILL_PATH}"
echo "Command file: ${COMMAND_PATH}"
echo
echo "Verify with:"
echo "  ls -la \"${INSTALLED_SKILL_PATH}\""
echo "  sed -n '1,120p' \"${COMMAND_PATH}\""
