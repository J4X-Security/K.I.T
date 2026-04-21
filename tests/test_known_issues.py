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
    def test_codex_wrapper_exposes_shared_engine_help(self) -> None:
        result = subprocess.run(
            ["python3", str(CODEX_WRAPPER), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("prepare-build", result.stdout)
        self.assertIn("finalize-build", result.stdout)

    def test_build_accepts_local_directory_input(self) -> None:
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
            output = tmp / "known-issues.md"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "build",
                    "--input",
                    str(audit_dir),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["canonical_issue_count"], 2)

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

    def test_finalize_build_can_extend_existing_register(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            existing_report = tmp / "existing.md"
            existing_report.write_text(
                textwrap.dedent(
                    """
                    # Admin can bypass cap checks during emergency mint

                    Severity: Medium
                    Summary: Emergency mint path skips cap validation.
                    Root Cause: emergencyMint omits the supply cap validation used by mint.
                    Impact: Total supply can exceed the configured cap.
                    Affected Component: TokenMinter.emergencyMint
                    """
                ).strip(),
                encoding="utf-8",
            )
            existing_output = tmp / "known-issues.md"
            subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "build",
                    "--input",
                    str(existing_report),
                    "--output",
                    str(existing_output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
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
            workspace = tmp / "workspace"
            state_file = tmp / "known-issues.json"
            prepare_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-build",
                    "--input",
                    str(new_report),
                    "--state-file",
                    str(state_file),
                    "--workspace-dir",
                    str(workspace),
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

            finalize_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "finalize-build",
                    "--state-file",
                    str(state_file),
                    "--merge-known",
                    str(existing_output),
                    "--output",
                    str(existing_output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(finalize_result.stdout)
            self.assertEqual(payload["canonical_issue_count"], 2)
            sidecar = json.loads(existing_output.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(len(sidecar["issues"]), 2)
            self.assertEqual(len(sidecar["sources"]), 2)

    def test_prepare_and_finalize_build_with_claude_extractions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report_a = tmp / "report-a.md"
            report_b = tmp / "report-b.md"
            report_a.write_text(
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
            report_b.write_text(
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
            workspace = tmp / "workspace"
            state_file = tmp / "known-issues.json"
            prepare_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "prepare-build",
                    "--input",
                    str(report_a),
                    "--input",
                    str(report_b),
                    "--state-file",
                    str(state_file),
                    "--workspace-dir",
                    str(workspace),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            prepare_payload = json.loads(prepare_result.stdout)
            self.assertEqual(Path(prepare_payload["state_file"]), state_file)
            self.assertTrue(state_file.exists())
            self.assertEqual(len(prepare_payload["sources"]), 2)

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
                            "aliases": ["Reward distributor transfer check is missing"],
                            "source_location": "Reward distributor findings",
                            "evidence_snippet": "Claim flow ignores failed token transfers.",
                            "extraction_confidence": "high",
                        }
                    ],
                },
                {
                    "source_id": "SRC-002",
                    "status": "partial",
                    "warnings": ["Formatting was irregular but one issue was recovered."],
                    "issues": [
                        {
                            "title": "Admin can bypass cap checks during emergency mint",
                            "summary": "Emergency mint path skips cap validation.",
                            "root_cause": "emergencyMint omits the supply cap validation used by mint.",
                            "impact": "Total supply can exceed the configured cap.",
                            "affected_component": "TokenMinter.emergencyMint",
                            "severity": "medium",
                            "aliases": [],
                            "source_location": "Emergency mint",
                            "evidence_snippet": "Emergency mint path skips cap validation.",
                            "extraction_confidence": "medium",
                        }
                    ],
                },
            ]
            state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

            output = tmp / "known-issues.md"
            finalize_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "finalize-build",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            finalize_payload = json.loads(finalize_result.stdout)
            self.assertEqual(finalize_payload["canonical_issue_count"], 2)
            sidecar = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(len(sidecar["sources"]), 2)
            self.assertEqual(sidecar["sources"][1]["extraction_status"], "partial")
            self.assertIn("Formatting was irregular", sidecar["sources"][1]["warnings"][0])

    def test_build_deduplicates_similar_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report_a = tmp / "report-a.md"
            report_b = tmp / "report-b.md"
            report_a.write_text(
                textwrap.dedent(
                    """
                    # Missing return value check in RewardDistributor

                    Severity: High
                    Summary: The reward distribution flow ignores the return value from the token transfer helper.
                    Root Cause: The RewardDistributor.claimRewards path does not verify whether the external token transfer succeeded.
                    Impact: Failed transfers can leave accounting updated while no tokens are delivered, causing user loss.
                    Affected Component: RewardDistributor.claimRewards
                    """
                ).strip(),
                encoding="utf-8",
            )
            report_b.write_text(
                textwrap.dedent(
                    """
                    # Unchecked transfer result desynchronizes reward accounting

                    Severity: High
                    Description: reward payouts continue even when the token transfer fails.
                    Root Cause: Because claimRewards updates internal balances before validating transfer success in RewardDistributor.claimRewards.
                    Impact: Users can be marked as paid without receiving tokens, which corrupts reward accounting.
                    Component: RewardDistributor.claimRewards
                    """
                ).strip(),
                encoding="utf-8",
            )
            output = tmp / "known-issues.md"

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "build",
                    "--input",
                    str(report_a),
                    "--input",
                    str(report_b),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["canonical_issue_count"], 1)
            markdown = output.read_text(encoding="utf-8")
            self.assertIn("KI-001", markdown)
            self.assertIn("RewardDistributor.claimRewards", markdown)

    def test_check_flags_new_vs_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report = tmp / "report.md"
            report.write_text(
                textwrap.dedent(
                    """
                    # Admin can bypass cap checks during emergency mint

                    Severity: Medium
                    Summary: The emergency mint path skips the normal cap enforcement.
                    Root Cause: emergencyMint omits the supply cap validation used by mint.
                    Impact: Total supply can exceed the configured cap.
                    Affected Component: TokenMinter.emergencyMint
                    """
                ).strip(),
                encoding="utf-8",
            )
            output = tmp / "known-issues.md"
            subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "build",
                    "--input",
                    str(report),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            known_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "check",
                    "--known",
                    str(output),
                    "--issue-text",
                    "Emergency mint in TokenMinter can exceed the configured supply cap because it skips cap validation.",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            known_payload = json.loads(known_result.stdout)
            self.assertEqual(known_payload["verdict"], "known")

            new_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "check",
                    "--known",
                    str(output),
                    "--issue-text",
                    "Reward vesting can be permanently blocked if the vesting schedule start time is never initialized.",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            new_payload = json.loads(new_result.stdout)
            self.assertEqual(new_payload["verdict"], "new")


if __name__ == "__main__":
    unittest.main()
