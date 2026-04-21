import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "claude-skill-known-issues" / "scripts" / "known_issues.py"


class KnownIssuesCliTests(unittest.TestCase):
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
