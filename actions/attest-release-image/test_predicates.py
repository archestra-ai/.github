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

    def malware(self, lines, *extra):
        log = self.root / "clamscan.log"
        log.write_text("\n".join(lines) + "\n")
        predicate = self.root / "malware.json"
        status, output = self.run_main("malware", log, predicate, "--image", f"ghcr.io/x/app@sha256:{DIGEST}",
                                       "--version", "1.5.3", "--database", "28189", *TIMES, *extra)
        return status, output, json.loads(predicate.read_text())

    def summary(self, scanned, infected):
        return ["", "----------- SCAN SUMMARY -----------", "Known viruses: 3628278", "Engine version: 1.5.3",
                "Scanned directories: 100", f"Scanned files: {scanned}", f"Infected files: {infected}",
                "Data scanned: 12.00 MiB", "Time: 8.123 sec (0 m 8 s)"]

    def test_a_clean_image_attests_its_scan_and_signature_database(self):
        status, _, predicate = self.malware(self.summary(960, 0))
        self.assertEqual(status, 0)
        self.assertEqual(predicate["result"], {"scannedFiles": 960, "scannedDirectories": 100, "infected": 0,
                                               "errors": 0, "detections": []})
        self.assertEqual(predicate["scanner"], {"uri": predicates.CLAMAV_URI, "version": "1.5.3",
                                                "database": {"version": "28189", "signatures": 3628278}})

    def test_malware_stops_the_release_unless_recorded(self):
        lines = ["/scan/opt/app/eicar.com: Eicar-Test-Signature FOUND",
                 "/scan/etc/unreadable: Access denied ERROR", *self.summary(961, 1)]
        status, output, predicate = self.malware(lines)
        self.assertEqual(status, 2)
        self.assertIn("/opt/app/eicar.com matches Eicar-Test-Signature", output)
        # Paths are the image's, not where the scan mounted it.
        self.assertEqual(predicate["result"]["detections"], [{"path": "/opt/app/eicar.com", "signature": "Eicar-Test-Signature"}])
        self.assertEqual(predicate["result"]["errors"], 1)
        status, _, predicate = self.malware(lines, "--on-detection", "record")
        self.assertEqual(status, 0)
        self.assertEqual(predicate["policy"], {"failOn": "nothing"})

    def test_an_unfinished_scan_is_not_attested(self):
        log = self.root / "clamscan.log"
        log.write_text("/scan/opt/app/eicar.com: Eicar-Test-Signature FOUND\n")
        with self.assertRaisesRegex(ValueError, "no clamscan summary"):
            predicates.main(["malware", str(log), str(self.root / "m.json"), "--image", f"ghcr.io/x/app@sha256:{DIGEST}",
                             "--version", "1.5.3", "--database", "1", *TIMES])

    def benchmark(self, details, waivers=None, key="docker.io/archestra/platform", *extra):
        report = self.root / "dockle.json"
        report.write_text(json.dumps({"summary": {"fatal": 0, "warn": 0, "info": 0, "skip": 0, "pass": 14},
                                      "details": details}))
        argv = []
        if waivers is not None:
            (self.root / "waivers.json").write_text(json.dumps({"waivers": waivers}))
            argv = ["--waivers", self.root / "waivers.json"]
        predicate = self.root / "benchmark.json"
        status, output = self.run_main("benchmark", report, predicate, "--image", f"ghcr.io/x/app@sha256:{DIGEST}",
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
                                        "statements": [{"timestamp": "2026-11-18T00:00:00Z", **s} for s in statements]}))
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
