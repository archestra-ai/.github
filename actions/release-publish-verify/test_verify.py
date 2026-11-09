"""Exercise the publication check, stubbing only the process boundary (gh,
docker, helm)."""

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("verify", Path(__file__).with_name("verify.py"))
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)

DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/archestra-ai/platform"
HUB = "docker.io/archestra/platform"
CHART = b"chart bytes"
REPO = "oci://europe-west1-docker.pkg.dev/p/archestra-public/helm-charts"
BOT = "archestra-releaser[bot]"


def publisher_run(**overrides):
    run = {"id": 7, "run_attempt": 2, "html_url": "https://github.com/archestra-ai/release-publisher/actions/runs/7",
           "display_title": "Publish archestra 1.2.3 [90-1]", "path": ".github/workflows/publish.yml",
           "head_branch": "main", "event": "repository_dispatch", "status": "completed", "conclusion": "success",
           "repository": {"full_name": "archestra-ai/release-publisher"},
           "actor": {"login": BOT}, "triggering_actor": {"login": BOT}}
    run.update(overrides)
    return run


class Tools:
    """Fake gh, docker and helm."""

    def __init__(self, runs=None, digests=None, chart=CHART, attestation_ok=True):
        self.runs = runs if runs is not None else [publisher_run(display_title="Publish archestra 1.2.3 [90-0]"),
                                                   publisher_run()]
        self.digests = digests or {}
        self.chart = chart
        self.attestation_ok = attestation_ok
        self.calls = []

    def run(self, args, env=None, **kwargs):
        self.calls.append((args, (env or {}).get("GH_TOKEN")))
        if args[:2] == ["gh", "api"]:
            return done(args, json.dumps({"workflow_runs": self.runs}))
        if args[:3] == ["gh", "attestation", "verify"]:
            if not self.attestation_ok:
                raise subprocess.CalledProcessError(1, args)
            return done(args, "")
        if args[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            name = args[4].rsplit(":", 1)[0]
            return done(args, json.dumps({"digest": self.digests.get(name, DIGEST)}))
        if args[:2] == ["helm", "pull"]:
            destination = Path(args[args.index("--destination") + 1])
            (destination / f"{args[2].rsplit('/', 1)[1]}-{args[4]}.tgz").write_bytes(self.chart)
            return done(args, "")
        raise AssertionError(f"Unexpected command {args}")


def done(args, stdout):
    return subprocess.CompletedProcess(args, 0, stdout, "")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.request = {"project": "archestra", "request_id": "90-1", "version": "1.2.3", "latest": [],
                        "images": {IMAGE: DIGEST, HUB: DIGEST},
                        "charts": {"run_id": 90, "sha256": {"archestra-platform": hashlib.sha256(CHART).hexdigest()}}}
        self.environment = {
            "GH_TOKEN": "app", "VERIFY_ATTESTATION_TOKEN": "github-token", "VERIFY_REQUESTER": BOT,
            "VERIFY_REQUEST_FILE": str(self.directory / "request.json"),
            "VERIFY_SIGNER_REPOSITORY": "archestra-ai/archestra",
            "VERIFY_SIGNER_WORKFLOW": "archestra-ai/archestra/.github/workflows/release-please.yml",
            "VERIFY_IMAGE_TAG": "{version}", "VERIFY_CHART_REPOSITORIES": f"archestra-platform {REPO}\n",
            "GITHUB_OUTPUT": str(self.directory / "output"),
        }

    def verify(self, tools, request=None, **overrides):
        (self.directory / "request.json").write_text(json.dumps(request or self.request))
        with mock.patch.object(VERIFY.subprocess, "run", side_effect=tools.run):
            VERIFY.main({**self.environment, **overrides})

    def test_a_published_request_passes(self):
        tools = Tools()
        self.verify(tools)
        commands = [args for args, _ in tools.calls]
        self.assertIn(["docker", "buildx", "imagetools", "inspect", f"{HUB}:1.2.3", "--format", "{{json .Manifest}}"],
                      commands)
        attestations = [(args, token) for args, token in tools.calls if args[1] == "attestation"]
        self.assertEqual(len(attestations), 2)
        self.assertEqual(attestations[0], (["gh", "attestation", "verify", f"oci://{HUB}@{DIGEST}", "--repo",
                                            "archestra-ai/archestra", "--signer-workflow",
                                            "archestra-ai/archestra/.github/workflows/release-please.yml",
                                            "--deny-self-hosted-runners"], "github-token"))
        self.assertIn("version=1.2.3\n", (self.directory / "output").read_text())

    def test_the_publisher_run_must_be_the_trusted_successful_one(self):
        for overrides, message in (
            ({"conclusion": "failure"}, "not successful"),
            ({"status": "waiting", "conclusion": None}, "not successful"),
            ({"head_branch": "feature"}, "wrong branch"),
            ({"path": ".github/workflows/other.yml"}, "wrong workflow"),
            ({"event": "workflow_dispatch"}, "wrong event"),
            ({"triggering_actor": {"login": "someone"}}, "wrong triggering actor"),
            ({"repository": {"full_name": "archestra-ai/fork"}}, "wrong repository"),
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                self.verify(Tools(runs=[publisher_run(**overrides)]))

    def test_no_publisher_run_for_the_request_fails(self):
        with self.assertRaisesRegex(ValueError, "No publisher run named"):
            self.verify(Tools(runs=[publisher_run(display_title="Publish archestra 1.2.3 [90-2]")]))

    def test_a_tag_holding_another_digest_fails(self):
        with self.assertRaisesRegex(ValueError, f"{HUB}:1.2.3 is sha256:b"):
            self.verify(Tools(digests={HUB: "sha256:" + "b" * 64}))

    def test_a_digest_without_the_attestation_fails(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify(Tools(attestation_ok=False))

    def test_a_chart_with_other_bytes_fails(self):
        with self.assertRaisesRegex(ValueError, "Chart archestra-platform 1.2.3 is sha256:"):
            self.verify(Tools(chart=b"other bytes"))

    def test_invalid_inputs_check_nothing(self):
        for overrides, request in (
            ({"VERIFY_REQUESTER": "someone"}, None),
            ({"VERIFY_SIGNER_WORKFLOW": "other/repo/.github/workflows/x.yml"}, None),
            ({"VERIFY_IMAGE_TAG": "latest"}, None),
            ({"VERIFY_CHART_REPOSITORIES": "archestra-platform https://example.com"}, None),
            ({"VERIFY_CHART_REPOSITORIES": ""}, None),
            ({}, {**self.request, "images": {IMAGE: "latest"}}),
            ({}, {**self.request, "version": "v1.2.3"}),
            ({}, {**self.request, "smoke": True}),
        ):
            with self.subTest(overrides=overrides, request=request):
                tools = Tools()
                with self.assertRaises(ValueError):
                    self.verify(tools, request=request, **overrides)
                self.assertFalse([args for args, _ in tools.calls if args[0] in ("docker", "helm")])


if __name__ == "__main__":
    unittest.main()
