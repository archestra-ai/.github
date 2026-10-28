"""Exercise the required review's completion guard without cloud credentials."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/archestra-runtime-review.yml"


def step(source, name):
    marker = "      - name: " + name + "\n"
    block = source.split(marker, 1)[1]
    return block.split("\n      - name:", 1)[0]


class ReviewFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text()
        cls.guard = step(cls.source, "Require a review result")
        cls.script = textwrap.dedent(cls.guard.split("        run: |\n", 1)[1])

    def check_result(self, outcome, status):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", self.script],
            env={**os.environ, "REVIEW_OUTCOME": outcome, "REVIEW_STATUS": status},
            capture_output=True, text=True, check=False,
        )

    def test_completed_reviews_and_intentional_skips_pass(self):
        # Findings are delivered through change requests; their count does not
        # change a completed review's status or fail an otherwise successful step.
        for status in ("success", "skipped", "nothing-to-do"):
            with self.subTest(status=status):
                self.assertEqual(self.check_result("success", status).returncode, 0)

    def test_runtime_and_authentication_failures_cannot_report_green(self):
        for status in ("unavailable", "aborted", "error", "unauthorized", "", "unexpected"):
            with self.subTest(status=status):
                result = self.check_result("success", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::Review did not complete", result.stdout)

    def test_tls_failure_missing_outputs_and_failed_tunnel_cannot_report_green(self):
        # TLS throws before status outputs exist; failed tunnel skips the action.
        for outcome, status in (("failure", ""), ("failure", "success"),
                                ("skipped", ""), ("", ""), ("cancelled", "success")):
            with self.subTest(outcome=outcome, status=status):
                self.assertNotEqual(self.check_result(outcome, status).returncode, 0)

    def test_workflow_propagates_operational_errors_and_always_cleans_up(self):
        tunnel = step(self.source, "Connect to private staging")
        review = step(self.source, "Review on the Archestra runtime")
        cleanup = step(self.source, "Close private staging access")
        for block in (tunnel, review):
            self.assertNotRegex(block, r"(?m)^        continue-on-error: true$")
        self.assertIn("          fail-on: error", review)
        self.assertIn("        id: review", review)
        self.assertIn("steps.iap.outcome == 'success'", review)
        self.assertIn("steps.review.outcome", self.guard)
        self.assertIn("steps.review.outputs.status", self.guard)
        self.assertIn("!cancelled()", self.guard)
        self.assertIn("always()", cleanup)
        # A failed review cannot skip cleanup, and a cleanup failure cannot mask it.
        self.assertIn("        continue-on-error: true", cleanup)

    def test_ineligible_prs_and_merge_groups_do_not_require_runtime_access(self):
        jobs = self.source.split("\njobs:\n", 1)[1]
        queue = jobs.split("  queue:\n", 1)[1].split("\n  # Decides", 1)[0]
        review_job = jobs.split("\n  review:\n", 1)[1]
        self.assertIn("github.event_name == 'merge_group'", queue)
        self.assertNotIn("id-token: write", queue)
        self.assertIn("    needs: gate", review_job)
        self.assertIn("    if: needs.gate.outputs.run == 'true'", review_job)
        self.assertIn("!github.event.pull_request.draft", self.source)
        self.assertIn('[[ "$ACTOR" == "$AUTHOR" ]] || exit 0', self.source)


if __name__ == "__main__":
    unittest.main()
