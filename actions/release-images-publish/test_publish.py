"""Exercise the release-images request, stubbing only the gh process boundary."""

import base64
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("publish", Path(__file__).with_name("publish.py"))
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)

DIGEST = "sha256:" + "a" * 64
IMAGE = "europe-west1-docker.pkg.dev/friendly-path-465518-r6/appa-public/appa-runtime"


class Gh:
    """A fake gh: records the dispatch and reports runs after a few polls."""

    def __init__(self, title="Publish openappa 1.2.3 [90-2]", polls_before_run=2, statuses=None,
                 conclusion="success"):
        self.title = title
        self.polls_before_run = polls_before_run
        # What each poll of the publisher run sees: a status, or "401" for a failed poll.
        self.statuses = list(statuses or ["queued", "waiting", "in_progress"])
        self.conclusion = conclusion
        self.dispatched = None
        self.calls = []
        # The token each poll of the publisher run used.
        self.poll_tokens = []

    def run(self, args, input=None, env=None, **kwargs):
        self.calls.append(args)
        if args[0] == "openssl":
            return subprocess.CompletedProcess(args, 0, b"signature", b"")
        if args[1:4] == ["api", "-X", "POST"]:
            self.dispatched = json.loads(input)
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1] == "api" and args[2] == "repos/archestra-ai/release-images/actions/runs/7":
            self.poll_tokens.append((env or {}).get("GH_TOKEN"))
            status = self.statuses.pop(0) if self.statuses else "completed"
            if status == "401":
                raise subprocess.CalledProcessError(1, args, "", "HTTP 401: Bad credentials")
            run = {"id": 7, "status": status, "conclusion": self.conclusion if status == "completed" else None}
            return subprocess.CompletedProcess(args, 0, json.dumps(run), "")
        if args[1] == "api":
            self.polls_before_run -= 1
            runs = [] if self.polls_before_run >= 0 else [
                {"id": 1, "display_title": "Publish openappa 1.2.3 [90-1]"},
                {"id": 7, "display_title": self.title}]
            return subprocess.CompletedProcess(args, 0, json.dumps({"workflow_runs": runs}), "")
        raise AssertionError(f"Unexpected command {args}")


class GitHubApi:
    """A fake GitHub REST API for minting and revoking App installation tokens."""

    def __init__(self):
        self.requests = []
        self.minted = 0

    def urlopen(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, request.get_header("Authorization"),
                              json.loads(request.data) if request.data else None))
        if request.full_url.endswith("/repos/archestra-ai/release-images/installation"):
            body = {"id": 55}
        elif request.full_url.endswith("/app/installations/55/access_tokens"):
            self.minted += 1
            body = {"token": f"minted-{self.minted}"}
        else:
            body = None
        return mock.MagicMock(**{"__enter__.return_value.read.return_value":
                                 b"" if body is None else json.dumps(body).encode()})


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        (self.directory / "images.json").write_text(json.dumps({IMAGE: DIGEST}))
        self.environment = {
            "PUBLISH_PROJECT": "openappa", "PUBLISH_VERSION": "1.2.3",
            "PUBLISH_IMAGES_FILE": str(self.directory / "images.json"), "PUBLISH_LATEST": "stable",
            "PUBLISH_CHARTS": "", "PUBLISH_SMOKE": "false",
            "PUBLISHER_REPOSITORY": "archestra-ai/release-images",
            "GITHUB_RUN_ID": "90", "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_OUTPUT": str(self.directory / "output"), "GH_TOKEN": "initial",
        }
        self.api = GitHubApi()

    def publish(self, gh, minutes_per_poll=0, **overrides):
        # A clock that moves on by minutes_per_poll at every sleep.
        clock = [1_000_000.0]

        def sleep(_seconds):
            clock[0] += minutes_per_poll * 60

        with mock.patch.object(PUBLISH.subprocess, "run", side_effect=gh.run), \
                mock.patch.object(PUBLISH.urllib.request, "urlopen", side_effect=self.api.urlopen), \
                mock.patch.object(PUBLISH.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(PUBLISH.time, "sleep", side_effect=sleep):
            PUBLISH.main({**self.environment, **overrides})

    def test_dispatches_this_run_attempt_and_watches_its_publisher_run(self):
        gh = Gh()
        self.publish(gh)
        self.assertEqual(gh.dispatched, {"event_type": "publish-release-images", "client_payload": {
            "project": "openappa", "request_id": "90-2", "version": "1.2.3",
            "latest": ["stable"], "images": {IMAGE: DIGEST}}})
        self.assertEqual(gh.calls[-1][:3], ["gh", "api", "repos/archestra-ai/release-images/actions/runs/7"])
        self.assertEqual(gh.poll_tokens, ["initial"] * 4)
        self.assertIn("run-url=https://github.com/archestra-ai/release-images/actions/runs/7",
                      (self.directory / "output").read_text())
        self.assertIn("run-id=7\n", (self.directory / "output").read_text())

    def test_a_hand_off_ends_once_the_publisher_run_exists(self):
        gh = Gh(statuses=["waiting"] * 1000)
        request_file = self.directory / "request.json"
        self.publish(gh, PUBLISH_WAIT="false", PUBLISH_REQUEST_FILE=str(request_file))
        self.assertEqual(gh.poll_tokens, [])
        self.assertIn("run-id=7\n", (self.directory / "output").read_text())
        self.assertEqual(json.loads(request_file.read_text()), gh.dispatched["client_payload"])

    def test_charts_are_named_by_file_and_bound_to_this_run(self):
        chart = self.directory / "appa-runtime-1.2.3.tgz"
        chart.write_bytes(b"chart")
        gh = Gh()
        self.publish(gh, PUBLISH_CHARTS=f"{chart}\n", PUBLISH_LATEST="")
        self.assertEqual(gh.dispatched["client_payload"]["charts"], {
            "run_id": 90, "sha256": {"appa-runtime": hashlib.sha256(b"chart").hexdigest()}})
        self.assertEqual(gh.dispatched["client_payload"]["latest"], [])

    def test_release_assets_are_named_by_file_and_bound_to_this_run(self):
        files = [self.directory / "terraform-provider-x_1.2.3_SHA256SUMS",
                 self.directory / "terraform-provider-x_1.2.3_linux_amd64.zip"]
        for path in files:
            path.write_bytes(path.name.encode())
        gh = Gh()
        self.publish(gh, PUBLISH_ASSETS="\n".join(map(str, files)) + "\n")
        self.assertEqual(gh.dispatched["client_payload"]["assets"], {
            "run_id": 90, "sha256": {path.name: hashlib.sha256(path.name.encode()).hexdigest() for path in files}})

    def test_smoke_requests(self):
        gh = Gh(title="Publish openappa 0.0.0-smoke.90 [90-2]")
        self.publish(gh, PUBLISH_VERSION="0.0.0-smoke.90", PUBLISH_SMOKE="true", PUBLISH_LATEST="")
        self.assertIs(gh.dispatched["client_payload"]["smoke"], True)
        self.assertNotIn("gate", gh.dispatched["client_payload"])

    def test_a_dry_run_can_ask_for_the_stable_approval(self):
        gh = Gh(title="Publish openappa 0.0.0-smoke.90 [90-2]")
        self.publish(gh, PUBLISH_VERSION="0.0.0-smoke.90", PUBLISH_SMOKE="true", PUBLISH_LATEST="",
                     PUBLISH_GATE="true")
        self.assertIs(gh.dispatched["client_payload"]["gate"], True)

    def test_a_failed_publisher_run_fails_the_step(self):
        for conclusion in ("failure", "cancelled"):
            with self.subTest(conclusion=conclusion), self.assertRaisesRegex(ValueError, f"concluded {conclusion}"):
                self.publish(Gh(conclusion=conclusion))

    def test_a_wait_longer_than_a_token_mints_new_tokens(self):
        # Six hours of polls, ten minutes apart: the App's key mints a new
        # token for the publisher repository before each one is 45 minutes old.
        gh = Gh(statuses=["waiting"] * 36)
        self.publish(gh, minutes_per_poll=10, PUBLISH_APP_ID="123", PUBLISH_APP_PRIVATE_KEY="-----KEY-----\\nabc")
        self.assertEqual(gh.poll_tokens[:5], ["initial"] * 5)
        self.assertEqual(gh.poll_tokens[5], "minted-1")
        self.assertEqual(len(set(gh.poll_tokens)), 1 + self.api.minted)
        self.assertGreaterEqual(self.api.minted, 7)
        mint = next(request for request in self.api.requests if request[1].endswith("/access_tokens"))
        self.assertEqual(mint[0], "POST")
        self.assertTrue(mint[2].startswith("Bearer "))
        self.assertEqual(mint[3], {"repositories": ["release-images"],
                                   "permissions": {"contents": "write", "actions": "read"}})
        claims = json.loads(base64.urlsafe_b64decode(mint[2].split()[1].split(".")[1] + "=="))
        self.assertEqual(claims["iss"], "123")
        # Every minted token is revoked when the wait ends.
        revoked = [request[2] for request in self.api.requests if request[0] == "DELETE"]
        self.assertEqual(revoked, [f"token minted-{n}" for n in range(1, self.api.minted + 1)])

    def test_a_failed_poll_mints_a_new_token_and_carries_on(self):
        gh = Gh(statuses=["waiting", "401", "waiting"])
        self.publish(gh, PUBLISH_APP_ID="123", PUBLISH_APP_PRIVATE_KEY="key")
        self.assertEqual(gh.poll_tokens, ["initial", "initial", "minted-1", "minted-1"])

    def test_transient_failures_are_retried_without_the_app_key(self):
        gh = Gh(statuses=["401", "401", "waiting"])
        self.publish(gh)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(len(gh.poll_tokens), 4)

    def test_only_repeated_failures_fail_the_wait(self):
        gh = Gh(statuses=["401"] * PUBLISH.MAX_POLL_FAILURES)
        with self.assertRaisesRegex(ValueError, "Could not read publisher run 7"):
            self.publish(gh)

    def test_a_publisher_that_never_starts_fails(self):
        with self.assertRaisesRegex(ValueError, "did not start"):
            self.publish(Gh(polls_before_run=PUBLISH.FIND_ATTEMPTS + 1))

    def test_invalid_inputs_send_nothing(self):
        (self.directory / "bad.json").write_text(json.dumps({IMAGE: "latest"}))
        chart = self.directory / "appa-runtime-9.9.9.tgz"
        chart.write_bytes(b"chart")
        for overrides in (
            {"PUBLISH_PROJECT": "Open APPA"},
            {"PUBLISH_VERSION": "v1.2.3"},
            {"PUBLISH_LATEST": "stable stable"},
            {"PUBLISH_LATEST": "always"},
            {"PUBLISH_SMOKE": "yes"},
            {"PUBLISH_GATE": "yes"},
            {"PUBLISH_WAIT": "no"},
            {"PUBLISH_IMAGES_FILE": str(self.directory / "bad.json")},
            {"PUBLISH_CHARTS": str(chart)},
            {"PUBLISH_ASSETS": str(self.directory / "missing.zip")},
            {"PUBLISH_ASSETS": f"{chart}\n{chart}"},
            {"PUBLISHER_REPOSITORY": "archestra-ai/release-images; rm -rf /"},
        ):
            with self.subTest(overrides=overrides):
                gh = Gh()
                with self.assertRaises(ValueError):
                    self.publish(gh, **overrides)
                self.assertIsNone(gh.dispatched)


if __name__ == "__main__":
    unittest.main()
