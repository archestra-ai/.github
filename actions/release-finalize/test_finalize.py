import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("finalize", Path(__file__).with_name("finalize.py"))
finalize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalize)

REPO = "archestra-ai/OpenAPPA"
WORKFLOW = ".github/workflows/release.yml"
REQUESTER = "openappa-releaser[bot]"


def config(**overrides):
    environment = {"GH_REPO": REPO, "FINALIZE_PROJECT": "openappa", "FINALIZE_RELEASE_WORKFLOW": WORKFLOW,
                   "FINALIZE_REQUESTER": REQUESTER, "FINALIZE_TAG_TEMPLATE": "v{version}", **overrides}
    return finalize.Config(environment)


class FakeGitHub:
    """The GitHub API paths finalize.py reads."""

    def __init__(self, runs=None, releases=None, publisher_runs=None):
        self.runs = runs or {}
        self.releases = releases or []
        self.publisher_runs = publisher_runs or []

    def __call__(self, path, token=None):
        if path.startswith(f"repos/{REPO}/actions/runs/"):
            return self.runs[int(path.rsplit("/", 1)[1])]
        if path == f"repos/{REPO}/releases?per_page=100":
            return self.releases
        if path.startswith("repos/archestra-ai/release-publisher/actions/workflows/publish.yml/runs"):
            assert token == "publisher-token"
            return {"workflow_runs": self.publisher_runs}
        raise AssertionError(path)


def release_run(branch="main", event="push", attempt=1, path=WORKFLOW):
    return {"path": path, "head_branch": branch, "event": event, "run_attempt": attempt,
            "repository": {"full_name": REPO}}


def publisher_run(version, request_id, conclusion="success", created="2026-11-15T10:00:00Z", actor=REQUESTER):
    return {"display_title": f"Publish openappa {version} [{request_id}]", "status": "completed",
            "conclusion": conclusion, "created_at": created, "triggering_actor": {"login": actor}}


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fetch(self, payload):
        def fetch(repository, run_id, name, directory):
            self.fetched = (repository, run_id, name)
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / "request.json").write_text(json.dumps(payload))
        return fetch

    def test_a_draft_release_of_the_kept_request_needs_publishing(self):
        github = FakeGitHub(runs={77: release_run(attempt=2)}, releases=[{"tag_name": "v0.43.0", "draft": True}])
        found = finalize.resolve(config(), "main", "77-2", self.root, request=github,
                                 fetch=self.fetch({"project": "openappa", "request_id": "77-2", "version": "0.43.0"}))
        self.assertEqual(self.fetched, (REPO, 77, "release-request-2"))
        self.assertEqual((found["version"], found["tag_name"], found["publish"]), ("0.43.0", "v0.43.0", True))
        published = FakeGitHub(runs={77: release_run(attempt=2)}, releases=[{"tag_name": "v0.43.0", "draft": False}])
        again = finalize.resolve(config(), "main", "77-2", self.root, request=published,
                                 fetch=self.fetch({"project": "openappa", "request_id": "77-2", "version": "0.43.0"}))
        self.assertFalse(again["publish"])

    def test_only_this_repository_s_release_run_and_request_are_trusted(self):
        good = {"project": "openappa", "request_id": "77-1", "version": "0.43.0"}
        cases = [
            ("not a request id", "main", "77", release_run(), good),
            ("not a release branch", "feature", "77-1", release_run(branch="feature"), good),
            ("another workflow", "main", "77-1", release_run(path=".github/workflows/ci.yml"), good),
            ("a pull request run", "main", "77-1", release_run(event="pull_request"), good),
            ("a later attempt than the run has", "main", "77-3", release_run(attempt=2), good),
            ("another project's request", "main", "77-1", release_run(), {**good, "project": "archestra"}),
            ("another request", "main", "77-1", release_run(), {**good, "request_id": "77-2"}),
            ("a dry run", "main", "77-1", release_run(), {**good, "smoke": True}),
        ]
        for label, branch, request_id, run, payload in cases:
            with self.subTest(label), self.assertRaises(ValueError):
                github = FakeGitHub(runs={77: run}, releases=[{"tag_name": "v0.43.0", "draft": True}])
                finalize.resolve(config(), branch, request_id, self.root, request=github, fetch=self.fetch(payload))


class SweepTests(unittest.TestCase):
    def test_dispatches_each_draft_whose_newest_publisher_run_succeeded(self):
        github = FakeGitHub(
            runs={10: release_run(), 20: release_run(branch="release/0.42"), 30: release_run()},
            releases=[{"tag_name": "v0.43.0", "draft": True}, {"tag_name": "v0.42.1", "draft": True},
                      {"tag_name": "v0.44.0", "draft": True}, {"tag_name": "v0.41.0", "draft": False}],
            publisher_runs=[
                publisher_run("0.43.0", "10-1"),
                publisher_run("0.42.1", "20-2"),
                # The newest request for 0.44.0 failed: an older success must not finish it.
                publisher_run("0.44.0", "30-1", created="2026-11-15T09:00:00Z"),
                publisher_run("0.44.0", "30-2", conclusion="failure", created="2026-11-15T11:00:00Z"),
                # A published release, and a request someone else sent.
                publisher_run("0.41.0", "40-1"),
                publisher_run("0.43.0", "50-1", created="2026-11-15T12:00:00Z", actor="someone"),
            ])
        found = finalize.sweep(config(FINALIZE_RELEASE_BRANCHES=r"main|release/\d+\.\d+"), "publisher-token",
                               request=github)
        self.assertEqual(found, [("release/0.42", "20-2"), ("main", "10-1")])

    def test_tags_of_another_shape_are_not_this_project_s_releases(self):
        github = FakeGitHub(releases=[{"tag_name": "platform-v1.4.0", "draft": True}])
        self.assertEqual(finalize.sweep(config(), "publisher-token", request=github), [])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.chart = self.root / "appa-runtime-0.43.0.tgz"
        self.chart.write_bytes(b"chart")
        self.binary = self.root / "appa_linux_amd64.tar.gz"
        self.binary.write_bytes(b"binary")
        self.request = self.root / "request.json"
        self.request.write_text(json.dumps({"charts": {"sha256": {"appa-runtime": hashlib.sha256(b"chart").hexdigest()}}}))

    def runner(self, draft=True):
        calls = []

        def run(*args):
            calls.append(args)
            if args[:3] == ("gh", "release", "view"):
                return json.dumps({"isDraft": draft})
            return ""
        return run, calls

    def test_uploads_the_files_and_publishes_the_draft(self):
        run, calls = self.runner()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(finalize.publish(config(), "v0.43.0", "0.43.0", self.request, [self.chart, self.binary], run=run))
        self.assertEqual(calls[1][:4], ("gh", "release", "upload", "v0.43.0"))
        self.assertEqual(calls[2], ("gh", "release", "edit", "v0.43.0", "--repo", REPO, "--draft=false",
                                    "--prerelease=false", "--latest"))

    def test_a_prerelease_never_becomes_latest_and_latest_false_is_kept(self):
        run, calls = self.runner()
        with contextlib.redirect_stdout(io.StringIO()):
            finalize.publish(config(), "v0.44.0-rc.1", "0.44.0-rc.1", self.request, [], run=run)
            finalize.publish(config(), "v0.43.0", "0.43.0", self.request, [], latest=False, run=run)
        edits = [call for call in calls if call[:3] == ("gh", "release", "edit")]
        self.assertEqual(edits[0][-2:], ("--prerelease", "--latest=false"))
        self.assertEqual(edits[1][-2:], ("--prerelease=false", "--latest=false"))

    def test_a_published_release_is_left_alone(self):
        run, calls = self.runner(draft=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(finalize.publish(config(), "v0.43.0", "0.43.0", self.request, [self.chart], run=run))
        self.assertEqual(len(calls), 1)

    def test_a_file_unlike_the_requested_one_publishes_nothing(self):
        self.chart.write_bytes(b"another chart")
        run, calls = self.runner()
        with self.assertRaisesRegex(ValueError, "not the file the request named"):
            finalize.publish(config(), "v0.43.0", "0.43.0", self.request, [self.chart], run=run)
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(ValueError, "not the tag of"):
            finalize.publish(config(), "v0.42.0", "0.43.0", self.request, [], run=run)


class ConfigTests(unittest.TestCase):
    def test_tags_map_to_versions_both_ways(self):
        archestra = config(FINALIZE_TAG_TEMPLATE="platform-v{version}")
        self.assertEqual(archestra.tag("1.4.32"), "platform-v1.4.32")
        self.assertEqual(archestra.version("platform-v1.5.0-rc.34"), "1.5.0-rc.34")
        self.assertIsNone(archestra.version("v1.4.32"))
        self.assertIsNone(config().version("vnext"))

    def test_rejects_settings_that_could_point_elsewhere(self):
        for overrides in [{"GH_REPO": "someone/OpenAPPA"}, {"FINALIZE_RELEASE_WORKFLOW": "release.yml"},
                          {"FINALIZE_TAG_TEMPLATE": "latest"}, {"FINALIZE_REQUESTER": "someone"},
                          {"FINALIZE_PUBLISHER_REPOSITORY": "elsewhere/publisher"}]:
            with self.subTest(overrides), self.assertRaises(ValueError):
                config(**overrides)


if __name__ == "__main__":
    unittest.main()
