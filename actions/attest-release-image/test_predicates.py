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

    def vex(self, statements, subject="docker.io/archestra/platform"):
        document = self.root / "project.openvex.json"
        document.write_text(json.dumps({"@context": "https://openvex.dev/ns/v0.2.0", "@id": "https://example.test/vex",
                                        "author": "Archestra", "timestamp": "2026-11-18T00:00:00Z", "version": 4,
                                        "statements": statements}))
        predicate = self.root / "vex.json"
        status, output = self.run_main("vex", document, predicate, "--image",
                                       f"ghcr.io/archestra-ai/ci/platform@sha256:{DIGEST}", "--subject-name", subject)
        return status, output, json.loads(predicate.read_text()) if predicate.exists() else None

    def test_vex_keeps_the_image_statements_pinned_to_its_digest_under_its_public_name(self):
        npm = {"@id": "pkg:npm/tar@6.2.0"}
        status, _, predicate = self.vex([
            {"vulnerability": {"name": "CVE-2026-0001"}, "status": "not_affected",
             "justification": "vulnerable_code_not_in_execute_path", "impact_statement": "Only extracts trusted archives.",
             "products": [{"@id": "pkg:oci/platform", "subcomponents": [npm]}, {"@id": "pkg:oci/agent-codex"}]},
            {"vulnerability": {"name": "CVE-2026-0002"}, "status": "under_investigation",
             "products": [{"@id": "pkg:oci/agent-codex"}]},
        ])
        self.assertEqual(status, 0)
        purl = f"pkg:oci/platform@sha256%3A{DIGEST}?repository_url=docker.io/archestra/platform"
        self.assertEqual(predicate["@context"], "https://openvex.dev/ns/v0.2.0")
        self.assertEqual((predicate["author"], predicate["version"]), ("Archestra", 4))
        # Only the statement about this image, with the other image's product dropped.
        self.assertEqual([s["vulnerability"]["name"] for s in predicate["statements"]], ["CVE-2026-0001"])
        self.assertEqual(predicate["statements"][0]["products"],
                         [{"@id": purl, "identifiers": {"purl": purl}, "hashes": {"sha-256": DIGEST}, "subcomponents": [npm]}])
        self.assertEqual(predicate["statements"][0]["impact_statement"], "Only extracts trusted archives.")

    def test_vex_attests_nothing_for_an_image_no_statement_names(self):
        status, output, predicate = self.vex([
            {"vulnerability": {"name": "CVE-2026-0002"}, "status": "fixed", "products": [{"@id": "pkg:oci/agent-codex"}]},
        ])
        self.assertEqual((status, predicate), (predicates.NO_VEX_STATEMENTS, None))
        self.assertIn("pkg:oci/platform", output)

    def test_vex_refuses_statements_a_scanner_could_not_trust(self):
        product = [{"@id": "pkg:oci/platform"}]
        for statement in [
            {"vulnerability": {"name": "CVE-1"}, "status": "not_affected", "products": product},  # no justification
            {"vulnerability": {"name": "CVE-1"}, "status": "affected", "products": product},  # no action statement
            {"vulnerability": {"name": "CVE-1"}, "status": "fine", "products": product},  # unknown status
            {"vulnerability": {"name": "CVE-1"}, "status": "not_affected", "justification": "trust me", "products": product},
            {"status": "fixed", "products": product},  # no vulnerability
        ]:
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                self.vex([statement])

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
