import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("benchmark", Path(__file__).with_name("benchmark.py"))
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)

DIGEST = "a" * 64
TIMES = ["--started", "2026-11-21T00:00:00Z", "--finished", "2026-11-21T00:01:00Z"]


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def run_main(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = benchmark.main([str(arg) for arg in argv])
        return status, output.getvalue()

    def benchmark(self, details, waivers=None, key="docker.io/archestra/platform", *extra):
        report = self.root / "dockle.json"
        report.write_text(json.dumps({"summary": {"fatal": 0, "warn": 0, "info": 0, "skip": 0, "pass": 14},
                                      "details": details}))
        argv = []
        if waivers is not None:
            (self.root / "waivers.json").write_text(json.dumps({"waivers": waivers}))
            argv = ["--waivers", self.root / "waivers.json"]
        predicate = self.root / "benchmark.json"
        status, output = self.run_main(report, predicate, "--image", f"ghcr.io/x/app@sha256:{DIGEST}",
                                       "--key", key, "--version", "0.4.15", *TIMES, *argv, *extra)
        return status, output, json.loads(predicate.read_text()) if predicate.exists() else None

    ROOT_USER = {"code": "CIS-DI-0001", "title": "Create a user for the container", "level": "WARN",
                 "alerts": ["Last user should not be root"]}
    CONTENT_TRUST = {"code": "CIS-DI-0005", "title": "Enable Content trust for Docker", "level": "INFO",
                     "alerts": ["export DOCKER_CONTENT_TRUST=1 before docker pull/build"]}
    ROOT_WAIVER = {"images": ["docker.io/archestra/platform"], "check": "CIS-DI-0001",
                   "reason": "The quickstart image starts as root for its bundled database."}

    def test_benchmark_records_info_findings_without_stopping_the_release(self):
        status, _, predicate = self.benchmark([self.CONTENT_TRUST])
        self.assertEqual(status, 0)
        self.assertEqual(predicate["result"]["passed"], 14)
        self.assertEqual(predicate["result"]["info"], 1)
        self.assertEqual(predicate["result"]["findings"][0]["check"], "CIS-DI-0005")
        self.assertEqual(predicate["policy"], {"failOn": ["FATAL", "WARN"], "waivers": []})

    def test_benchmark_stops_the_release_on_an_unwaived_warning_unless_recorded(self):
        status, output, _ = self.benchmark([self.ROOT_USER])
        self.assertEqual(status, 2)
        self.assertIn("fails CIS-DI-0001 (WARN)", output)
        status, _, predicate = self.benchmark([self.ROOT_USER], None, "docker.io/archestra/platform", "--on-failure", "record")
        self.assertEqual(status, 0)
        self.assertEqual(predicate["policy"]["failOn"], [])
        self.assertEqual(predicate["result"]["warn"], 1)

    def test_benchmark_waiver_covers_only_the_images_it_names(self):
        status, _, predicate = self.benchmark([self.ROOT_USER, self.CONTENT_TRUST], [self.ROOT_WAIVER])
        self.assertEqual(status, 0)
        self.assertEqual(predicate["result"]["waived"], 1)
        self.assertEqual(predicate["result"]["findings"][0]["waiver"], self.ROOT_WAIVER["reason"])
        self.assertEqual(predicate["policy"]["waivers"], [{"check": "CIS-DI-0001", "reason": self.ROOT_WAIVER["reason"]}])
        # A variant of the image is another key: the waiver does not reach it.
        status, _, _ = self.benchmark([self.ROOT_USER], [self.ROOT_WAIVER], "docker.io/archestra/platform:slim")
        self.assertEqual(status, 2)

    def test_benchmark_waiver_matches_image_globs(self):
        waiver = {"images": ["*/agent-*"], "check": "CIS-DI-0006",
                  "reason": "Kubernetes runs it and ignores HEALTHCHECK; the pod has probes."}
        healthcheck = {"code": "CIS-DI-0006", "title": "Add HEALTHCHECK instruction to the container image",
                       "level": "WARN", "alerts": ["not found HEALTHCHECK statement"]}
        status, _, _ = self.benchmark([healthcheck], [waiver], "ghcr.io/archestra-ai/agent-codex")
        self.assertEqual(status, 0)
        status, _, _ = self.benchmark([healthcheck], [waiver], "ghcr.io/archestra-ai/p4-shim")
        self.assertEqual(status, 2)

    def test_benchmark_waiver_with_alerts_covers_only_those_alerts(self):
        secrets = {"code": "CIS-DI-0010", "title": "Do not store credential in environment variables/files",
                   "level": "FATAL", "alerts": ["Suspicious filename found : opt/venv/lib/mcp/server/auth/settings.py"]}
        waiver = {"images": ["*/mcp-server-base"], "check": "CIS-DI-0010", "alerts": ["Suspicious filename found : opt/venv/lib/*"],
                  "reason": "Package source named like a settings file; the secret scan reads its contents."}
        status, _, predicate = self.benchmark([secrets], [waiver], "ghcr.io/archestra-ai/mcp-server-base")
        self.assertEqual(status, 0)
        self.assertEqual(predicate["policy"]["waivers"][0]["alerts"], waiver["alerts"])
        secrets = {**secrets, "alerts": [*secrets["alerts"], "Suspicious ENV key found : API_TOKEN"]}
        status, output, _ = self.benchmark([secrets], [waiver], "ghcr.io/archestra-ai/mcp-server-base")
        self.assertEqual(status, 2)
        self.assertIn("API_TOKEN", output)

    def test_benchmark_warns_about_a_waiver_the_image_no_longer_needs(self):
        status, output, predicate = self.benchmark([], [self.ROOT_WAIVER])
        self.assertEqual(status, 0)
        self.assertIn("passes CIS-DI-0001; remove its waiver", output)
        self.assertEqual(predicate["result"]["waived"], 0)

    def test_benchmark_refuses_waivers_without_a_reason_or_check(self):
        for waiver in ({"images": ["*"], "check": "CIS-DI-0001", "reason": ""},
                       {"images": ["*"], "check": "rule-1", "reason": "Because it does not apply here."},
                       {"images": [], "check": "CIS-DI-0001", "reason": "Because it does not apply here."},
                       {"images": ["*"], "check": "CIS-DI-0001"},
                       {"images": ["*"], "check": "CIS-DI-0001", "reason": "Because it does not apply here.", "alerts": []}):
            with self.subTest(waiver=waiver), self.assertRaises(ValueError):
                self.benchmark([self.ROOT_USER], [waiver])

    def test_benchmark_refuses_a_report_that_is_not_dockle(self):
        with self.assertRaises(ValueError):
            self.benchmark([{"code": "CIS-DI-0001", "level": "CRITICAL", "alerts": []}])



if __name__ == "__main__":
    unittest.main()
