import json
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "claude-skill-known-issues" / "scripts" / "known_issues.py"
CODEX_WRAPPER = ROOT / "codex-skill-known-issues" / "scripts" / "known_issues.py"


class KnownIssuesCliTests(unittest.TestCase):
    def test_codex_wrapper_exposes_staged_commands(self) -> None:
        result = subprocess.run(
            ["python3", str(CODEX_WRAPPER), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("prepare-build", result.stdout)
        self.assertIn("finalize-build", result.stdout)
        self.assertIn("prepare-check", result.stdout)
        self.assertNotIn(" build ", result.stdout)
        self.assertNotIn(" check ", result.stdout)

    def test_parse_github_repo_and_folder_urls(self) -> None:
        spec = importlib.util.spec_from_file_location("known_issues", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        repo_url = "https://github.com/openai/example-audits"
        folder_url = "https://github.com/openai/example-audits/tree/main/reports/2024"
        self.assertEqual(
            module.parse_github_container_url(repo_url),
            {"owner": "openai", "repo": "example-audits", "ref": "", "path": ""},
        )
        self.assertEqual(
            module.parse_github_container_url(folder_url),
            {"owner": "openai", "repo": "example-audits", "ref": "main", "path": "reports/2024"},
        )

    def test_prepare_build_accepts_local_directory_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            audit_dir = tmp / "audits"
            audit_dir.mkdir()
            (audit_dir / "report-one.md").write_text(
                textwrap.dedent(
                    """
                    # Missing transfer validation

                    Severity: High
                    Summary: Claim flow ignores failed token transfers.
                    Root Cause: claimRewards updates accounting before checking transfer success.
                    Impact: Users can be recorded as paid without receiving rewards.
                    Affected Component: RewardDistributor.claimRewards
                    """
                ).strip(),
                encoding="utf-8",
            )
            (audit_dir / "report-two.md").write_text(
                textwrap.dedent(
                    """
                    # Cap bypass in emergency mint

                    Severity: Medium
                    Summary: Emergency mint path skips cap validation.
                    Root Cause: emergencyMint omits the supply cap validation used by mint.
                    Impact: Total supply can exceed the configured cap.
                    Affected Component: TokenMinter.emergencyMint
                    """
                ).strip(),
                encoding="utf-8",
            )
            state_file = tmp / "known-issues.json"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-build",
                    "--input",
                    str(audit_dir),
                    "--state-file",
                    str(state_file),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["sources"]), 2)
            self.assertTrue(state_file.exists())

    def test_finalize_build_requires_llm_canonical_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report = tmp / "report.md"
            report.write_text(
                textwrap.dedent(
                    """
                    # Reward distributor transfer check is missing

                    Severity: High
                    Summary: Claim flow ignores failed token transfers.
                    Root Cause: claimRewards updates accounting before checking transfer success.
                    Impact: Users can be recorded as paid without receiving rewards.
                    Affected Component: RewardDistributor.claimRewards
                    """
                ).strip(),
                encoding="utf-8",
            )
            state_file = tmp / "known-issues.json"
            subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-build",
                    "--input",
                    str(report),
                    "--state-file",
                    str(state_file),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            state_payload = json.loads(state_file.read_text(encoding="utf-8"))
            state_payload["source_results"] = [
                {
                    "source_id": "SRC-001",
                    "status": "ok",
                    "warnings": [],
                    "issues": [
                        {
                            "title": "Unchecked transfer result desynchronizes reward accounting",
                            "summary": "Claim flow ignores failed token transfers.",
                            "root_cause": "claimRewards updates accounting before checking transfer success.",
                            "impact": "Users can be marked as paid without receiving rewards.",
                            "affected_component": "RewardDistributor.claimRewards",
                            "severity": "high",
                            "aliases": [],
                            "source_location": "Reward distributor findings",
                            "evidence_snippet": "Claim flow ignores failed token transfers.",
                            "extraction_confidence": "high",
                        }
                    ],
                }
            ]
            state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "finalize-build",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(state_file),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("canonical_issues", result.stderr)

    def test_finalize_build_can_extend_existing_register(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            existing_known = tmp / "existing-known.json"
            existing_known.write_text(
                json.dumps(
                    {
                        "inputs": ["existing.md"],
                        "sources": [],
                        "issues": [
                            {
                                "issue_id": "KI-001",
                                "title": "Admin can bypass cap checks during emergency mint",
                                "summary": "Emergency mint path skips cap validation.",
                                "root_cause": "emergencyMint omits the supply cap validation used by mint.",
                                "impact": "Total supply can exceed the configured cap.",
                                "affected_component": "TokenMinter.emergencyMint",
                                "severity": "medium",
                                "aliases": ["Admin can bypass cap checks during emergency mint"],
                                "source_reports": ["existing.md"],
                                "canonical_key": "tokenminter-emergencymint-cap",
                                "source_ids": ["EXISTING"],
                                "evidence": [],
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            new_report = tmp / "new.md"
            new_report.write_text(
                textwrap.dedent(
                    """
                    # Reward distributor transfer check is missing

                    Severity: High
                    Summary: Claim flow ignores failed token transfers.
                    Root Cause: claimRewards updates accounting before checking transfer success.
                    Impact: Users can be recorded as paid without receiving rewards.
                    Affected Component: RewardDistributor.claimRewards
                    """
                ).strip(),
                encoding="utf-8",
            )
            state_file = tmp / "known-issues.json"
            prepare_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-build",
                    "--input",
                    str(new_report),
                    "--merge-known",
                    str(existing_known),
                    "--state-file",
                    str(state_file),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(prepare_result.stdout)["existing_issue_count"], 1)

            state_payload = json.loads(state_file.read_text(encoding="utf-8"))
            state_payload["source_results"] = [
                {
                    "source_id": "SRC-001",
                    "status": "ok",
                    "warnings": [],
                    "issues": [
                        {
                            "title": "Unchecked transfer result desynchronizes reward accounting",
                            "summary": "Claim flow ignores failed token transfers.",
                            "root_cause": "claimRewards updates accounting before checking transfer success.",
                            "impact": "Users can be marked as paid without receiving rewards.",
                            "affected_component": "RewardDistributor.claimRewards",
                            "severity": "high",
                            "aliases": [],
                            "source_location": "Reward distributor findings",
                            "evidence_snippet": "Claim flow ignores failed token transfers.",
                            "extraction_confidence": "high",
                        }
                    ],
                }
            ]
            state_payload["canonical_issues"] = [
                {
                    "title": "Admin can bypass cap checks during emergency mint",
                    "summary": "Emergency mint path skips cap validation.",
                    "root_cause": "emergencyMint omits the supply cap validation used by mint.",
                    "impact": "Total supply can exceed the configured cap.",
                    "affected_component": "TokenMinter.emergencyMint",
                    "severity": "medium",
                    "aliases": ["Admin can bypass cap checks during emergency mint"],
                    "source_reports": ["existing.md"],
                    "source_ids": ["EXISTING"],
                    "evidence": [],
                },
                {
                    "title": "Unchecked transfer result desynchronizes reward accounting",
                    "summary": "Claim flow ignores failed token transfers.",
                    "root_cause": "claimRewards updates accounting before checking transfer success.",
                    "impact": "Users can be marked as paid without receiving rewards.",
                    "affected_component": "RewardDistributor.claimRewards",
                    "severity": "high",
                    "aliases": ["Reward distributor transfer check is missing"],
                    "source_reports": [str(new_report)],
                    "source_ids": ["SRC-001"],
                    "evidence": [],
                },
            ]
            state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "finalize-build",
                    "--state-file",
                    str(state_file),
                    "--merge-known",
                    str(existing_known),
                    "--output",
                    str(state_file),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["canonical_issue_count"], 2)
            final_payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(len(final_payload["issues"]), 2)

    def test_prepare_check_emits_known_issues_and_all_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            known = tmp / "known-issues.json"
            known.write_text(
                json.dumps(
                    {
                        "inputs": ["existing.md"],
                        "sources": [],
                        "issues": [
                            {
                                "issue_id": "KI-001",
                                "title": "Admin can bypass cap checks during emergency mint",
                                "summary": "Emergency mint path skips cap validation.",
                                "root_cause": "emergencyMint omits the supply cap validation used by mint.",
                                "impact": "Total supply can exceed the configured cap.",
                                "affected_component": "TokenMinter.emergencyMint",
                                "severity": "medium",
                                "aliases": ["Admin can bypass cap checks during emergency mint"],
                                "source_reports": ["existing.md"],
                                "canonical_key": "tokenminter-emergencymint-cap",
                                "source_ids": ["EXISTING"],
                                "evidence": [],
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            findings = tmp / "findings.md"
            findings.write_text(
                textwrap.dedent(
                    """
                    # Admin can bypass cap checks during emergency mint

                    Severity: Medium
                    Summary: The emergency mint path skips cap validation.
                    Root Cause: emergencyMint omits the supply cap validation used by mint.
                    Impact: Total supply can exceed the configured cap.
                    Affected Component: TokenMinter.emergencyMint

                    # Reward vesting can be permanently blocked

                    Severity: Medium
                    Summary: Vesting claims can be blocked forever.
                    Root Cause: The vesting schedule start time may remain unset.
                    Impact: Users may never be able to claim vested rewards.
                    Affected Component: Vesting.claim
                    """
                ).strip(),
                encoding="utf-8",
            )
            staged = tmp / "known-issues-check.json"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-check",
                    "--known",
                    str(known),
                    "--issue-file",
                    str(findings),
                    "--output",
                    str(staged),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["known_issue_count"], 1)
            staged_payload = json.loads(staged.read_text(encoding="utf-8"))
            self.assertEqual(len(staged_payload["known_issues"]), 1)
            self.assertIn("llm_contract", staged_payload)
            self.assertIn("finding_extraction", staged_payload["llm_contract"])
            self.assertIn("duplicate_check", staged_payload["llm_contract"])
            self.assertIn("Admin can bypass cap checks during emergency mint", staged_payload["report_text"])
            self.assertIn("Reward vesting can be permanently blocked", staged_payload["report_text"])


if __name__ == "__main__":
    unittest.main()
