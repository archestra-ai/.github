"""Exercise the release-images request, stubbing only the gh process boundary."""

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

    def __init__(self, title="Publish openappa 1.2.3 [90-2]", polls_before_run=2, watch_status=0):
        self.title = title
        self.polls_before_run = polls_before_run
        self.watch_status = watch_status
        self.dispatched = None
        self.calls = []

    def run(self, args, input=None, **kwargs):
        self.calls.append(args)
        if args[1:4] == ["api", "-X", "POST"]:
            self.dispatched = json.loads(input)
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1] == "api":
            self.polls_before_run -= 1
            runs = [] if self.polls_before_run >= 0 else [
                {"id": 1, "display_title": "Publish openappa 1.2.3 [90-1]"},
                {"id": 7, "display_title": self.title}]
            return subprocess.CompletedProcess(args, 0, json.dumps({"workflow_runs": runs}), "")
        if args[1:3] == ["run", "watch"]:
            if self.watch_status:
                raise subprocess.CalledProcessError(self.watch_status, args)
            return subprocess.CompletedProcess(args, 0, None, None)
        raise AssertionError(f"Unexpected command {args}")


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
            "GITHUB_OUTPUT": str(self.directory / "output"),
        }

    def publish(self, gh, **overrides):
        with mock.patch.object(PUBLISH.subprocess, "run", side_effect=gh.run), \
                mock.patch.object(PUBLISH.time, "sleep"):
            PUBLISH.main({**self.environment, **overrides})

    def test_dispatches_this_run_attempt_and_watches_its_publisher_run(self):
        gh = Gh()
        self.publish(gh)
        self.assertEqual(gh.dispatched, {"event_type": "publish-release-images", "client_payload": {
            "project": "openappa", "request_id": "90-2", "version": "1.2.3",
            "latest": ["stable"], "images": {IMAGE: DIGEST}}})
        self.assertEqual(gh.calls[-1][:3], ["gh", "run", "watch"])
        self.assertEqual(gh.calls[-1][3], "7")
        self.assertIn("run-url=https://github.com/archestra-ai/release-images/actions/runs/7",
                      (self.directory / "output").read_text())

    def test_charts_are_named_by_file_and_bound_to_this_run(self):
        chart = self.directory / "appa-runtime-1.2.3.tgz"
        chart.write_bytes(b"chart")
        gh = Gh()
        self.publish(gh, PUBLISH_CHARTS=f"{chart}\n", PUBLISH_LATEST="")
        self.assertEqual(gh.dispatched["client_payload"]["charts"], {
            "run_id": 90, "sha256": {"appa-runtime": hashlib.sha256(b"chart").hexdigest()}})
        self.assertEqual(gh.dispatched["client_payload"]["latest"], [])

    def test_smoke_requests(self):
        gh = Gh(title="Publish openappa 0.0.0-smoke.90 [90-2]")
        self.publish(gh, PUBLISH_VERSION="0.0.0-smoke.90", PUBLISH_SMOKE="true", PUBLISH_LATEST="")
        self.assertIs(gh.dispatched["client_payload"]["smoke"], True)

    def test_a_failed_publisher_run_fails_the_step(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish(Gh(watch_status=1))

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
            {"PUBLISH_IMAGES_FILE": str(self.directory / "bad.json")},
            {"PUBLISH_CHARTS": str(chart)},
            {"PUBLISHER_REPOSITORY": "archestra-ai/release-images; rm -rf /"},
        ):
            with self.subTest(overrides=overrides):
                gh = Gh()
                with self.assertRaises(ValueError):
                    self.publish(gh, **overrides)
                self.assertIsNone(gh.dispatched)


if __name__ == "__main__":
    unittest.main()
