import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("predicates", Path(__file__).with_name("predicates.py"))
predicates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(predicates)

DIGEST = "a" * 64
TIMES = ["--started", "2026-11-14T20:00:00Z", "--finished", "2026-11-14T20:01:00Z"]


def finding(detector, verified=False, error=None):
    value = {"DetectorName": detector, "Verified": verified, "Raw": "the-secret", "Redacted": "the-sec***",
             "SourceMetadata": {"Data": {"Docker": {"file": "/app/.env", "layer": "sha256:" + "b" * 64}}}}
    if error:
        value["VerificationError"] = error
    return json.dumps(value)


class PredicateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def run_main(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = predicates.main([str(arg) for arg in argv])
        return status, output.getvalue()

    def secrets(self, lines, *extra):
        findings = self.root / "findings.jsonl"
        findings.write_text("\n".join(lines) + "\n")
        predicate = self.root / "secrets.json"
        status, output = self.run_main("secrets", findings, predicate, "--image", f"ghcr.io/x/app@sha256:{DIGEST}",
                                       "--version", "3.99.8", *TIMES, *extra)
        return status, output, json.loads(predicate.read_text())

    def test_counts_findings_by_result_and_detector_without_secrets(self):
        status, _, predicate = self.secrets([
            '{"level":"info","msg":"scanning"}',
            finding("Github"),
            finding("Github"),
            finding("AWS", error="timeout"),
            "not json",
        ])
        self.assertEqual(status, 0)
        self.assertEqual({k: predicate["result"][k] for k in ("verified", "unknown", "unverified")},
                         {"verified": 0, "unknown": 1, "unverified": 2})
        self.assertEqual(predicate["result"]["detectors"], [
            {"name": "AWS", "result": "unknown", "count": 1},
            {"name": "Github", "result": "unverified", "count": 2},
        ])
        # A public attestation must not leak the secret, its redacted form or where it is.
        text = json.dumps(predicate)
        for leak in ("the-secret", "the-sec", "/app/.env", "b" * 64):
            self.assertNotIn(leak, text)

    def test_a_live_secret_stops_the_release_unless_recorded(self):
        status, output, predicate = self.secrets([finding("Slack", verified=True), finding("Github")])
        self.assertEqual(status, 2)
        self.assertIn("::error::", output)
        self.assertIn("Slack", output)
        self.assertEqual(predicate["policy"], {"failOn": "verified"})
        status, _, predicate = self.secrets([finding("Slack", verified=True)], "--on-verified", "record")
        self.assertEqual(status, 0)
        self.assertEqual(predicate["result"]["verified"], 1)
        self.assertEqual(predicate["policy"], {"failOn": "nothing"})

    def test_a_clean_image_attests_zero_findings(self):
        status, _, predicate = self.secrets([])
        self.assertEqual(status, 0)
        self.assertEqual(predicate["result"]["detectors"], [])
        self.assertEqual(predicate["scanner"], {"uri": predicates.TRUFFLEHOG_URI, "version": "3.99.8"})

    def test_vulnerability_report_is_wrapped_in_the_cosign_predicate(self):
        report = self.root / "scout.json"
        report.write_text('{"runs": [{"results": []}]}')
        predicate_path = self.root / "vuln.json"
        status, _ = self.run_main("vulnerabilities", report, predicate_path, "--scanner-uri",
                                  "https://github.com/docker/scout-cli", "--scanner-version", "1.18.0", *TIMES)
        predicate = json.loads(predicate_path.read_text())
        self.assertEqual(status, 0)
        self.assertEqual(predicate["scanner"]["result"], {"runs": [{"results": []}]})
        self.assertEqual(predicate["scanner"]["uri"], "https://github.com/docker/scout-cli")
        self.assertEqual(set(predicate), {"invocation", "scanner", "metadata"})

    def test_subjects_name_the_digest_under_its_public_name(self):
        output = self.root / "subjects.txt"
        status, _ = self.run_main("subjects", f"ghcr.io/archestra-ai/ci/platform:1.5.0@sha256:{DIGEST}",
                                  "docker.io/archestra/platform", output)
        self.assertEqual(status, 0)
        self.assertEqual(output.read_text(), f"{DIGEST}  docker.io/archestra/platform\n")
        for image, name in [("ghcr.io/x/app:1.0", "ghcr.io/x/app"),  # not pinned by digest
                            (f"ghcr.io/x/app@sha256:{DIGEST}", "ghcr.io/x/app:1.0"),  # tagged name
                            (f"ghcr.io/x/app@sha256:{DIGEST}", "app")]:  # no registry
            with self.subTest(image=image, name=name), self.assertRaises(ValueError):
                self.run_main("subjects", image, name, output)


if __name__ == "__main__":
    unittest.main()
