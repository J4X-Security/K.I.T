#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_SKILL_DIR="${REPO_ROOT}/codex-skill-known-issues"
CODEX_SKILLS_DIR="${HOME}/.codex/skills"
INSTALLED_SKILL_PATH="${CODEX_SKILLS_DIR}/known-issues-aggregator"

if [[ ! -d "${SOURCE_SKILL_DIR}" ]]; then
  echo "error: Codex skill source directory not found: ${SOURCE_SKILL_DIR}" >&2
  exit 1
fi

mkdir -p "${CODEX_SKILLS_DIR}"
ln -sfn "${SOURCE_SKILL_DIR}" "${INSTALLED_SKILL_PATH}"

echo "Installed KIT / Known Issue Triager for Codex"
echo "Skill: known-issues-aggregator"
echo "Skill path: ${INSTALLED_SKILL_PATH}"
echo
echo "Verify with:"
echo "  ls -la \"${INSTALLED_SKILL_PATH}\""
echo "  sed -n '1,120p' \"${INSTALLED_SKILL_PATH}/SKILL.md\""
echo "  python3 \"${INSTALLED_SKILL_PATH}/scripts/known_issues.py\" --help"
