import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "verify_base_images", Path(__file__).with_name("verify_base_images.py")
)
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify  # dataclasses resolve annotations through it
SPEC.loader.exec_module(verify)

TRUST = """
[[signed]]
repository = "ghcr.io/example/*"
method = "github"
owner = "example"

[[signed]]
repository = "ghcr.io/example/tool"
method = "github"
repo = "example/tool"
signer_workflow = "example/tool/.github/workflows/release.yml"

[[signed]]
repository = "gcr.io/distroless/*"
method = "cosign"
issuer = "https://accounts.google.com"
identity = "keyless@distroless.iam.gserviceaccount.com"

[[unsigned]]
repository = "docker.io/library/*"
reason = "official images are unsigned"
"""
DIGEST = "@sha256:" + "a" * 64


class FakeTools:
    """gh and cosign: references in `good` verify, anything else fails."""

    def __init__(self, good=(), flaky=0):
        self.good = set(good)
        self.flaky = flaky
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        reference = command[3] if command[0] == "gh" else command[2]
        reference = reference.removeprefix("oci://")
        if self.flaky:
            self.flaky -= 1
            return subprocess.CompletedProcess(command, 1, "", "Error: connection reset")
        if reference in self.good:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "Error: no matching attestations found")


class VerifyBaseImagesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        trust = self.root / "trust.toml"
        trust.write_text(TRUST)
        self.trust = verify.load_trust(trust)
        self.tools = FakeTools()
        self.sleeps = []

    def tearDown(self):
        self.directory.cleanup()

    def write(self, files):
        for path, content in files.items():
            target = self.root / "repo" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(textwrap.dedent(content))

    def check(self, files=None, base_ref=None):
        if files:
            self.write(files)
        verifier = verify.Verifier(run=self.tools, sleep=self.sleeps.append)
        return verify.check(self.root / "repo", self.trust, verifier, base_ref=base_ref)

    def statuses(self, results):
        return [(r.line, r.status, r.level) for r in results]

    # --- trust list ---------------------------------------------------------------

    def test_signed_image_that_verifies_passes(self):
        self.tools.good.add(f"gcr.io/distroless/static:nonroot{DIGEST}")
        results = self.check({"Dockerfile": f"FROM gcr.io/distroless/static:nonroot{DIGEST}\n"})
        self.assertEqual(self.statuses(results), [(1, "verified", "ok")])
        self.assertEqual(self.tools.commands[0][:3], ["cosign", "verify", f"gcr.io/distroless/static:nonroot{DIGEST}"])
        self.assertIn("keyless@distroless.iam.gserviceaccount.com", self.tools.commands[0])

    def test_signed_image_that_does_not_verify_fails(self):
        results = self.check({"Dockerfile": f"FROM ghcr.io/example/app:1{DIGEST}\n"})
        self.assertEqual(self.statuses(results), [(1, "failed", "fail")])
        self.assertIn("no matching attestations found", results[0].detail)

    def test_unsigned_listed_image_passes_without_running_a_tool(self):
        results = self.check({"Dockerfile": f"FROM node:24-slim{DIGEST}\nFROM python:3.13\n"})
        self.assertEqual(self.statuses(results), [(1, "unsigned", "ok"), (2, "unsigned", "ok")])
        self.assertEqual(self.tools.commands, [])

    def test_image_missing_from_the_trust_list_fails(self):
        results = self.check({"Dockerfile": f"FROM quay.io/someone/thing:1{DIGEST}\n"})
        self.assertEqual(self.statuses(results), [(1, "unknown", "fail")])
        self.assertIn("quay.io/someone/thing", results[0].detail)

    def test_the_most_specific_entry_decides_how_to_verify(self):
        self.tools.good.add(f"ghcr.io/example/tool:2{DIGEST}")
        self.check({"Dockerfile": f"FROM ghcr.io/example/tool:2{DIGEST}\n"})
        command = self.tools.commands[0]
        self.assertEqual(command[:4], ["gh", "attestation", "verify", f"oci://ghcr.io/example/tool:2{DIGEST}"])
        self.assertEqual(command[4:], ["--repo", "example/tool",
                                       "--signer-workflow", "example/tool/.github/workflows/release.yml"])

    def test_docker_hub_names_are_matched_in_full(self):
        for written, expected in [("node:24", "docker.io/library/node"),
                                  (f"library/node{DIGEST}", "docker.io/library/node"),
                                  ("index.docker.io/archestra/platform:1.4", "docker.io/archestra/platform"),
                                  ("localhost:5000/app:1", "localhost:5000/app"),
                                  (f"GHCR.io/Example/App{DIGEST}", "ghcr.io/example/app")]:
            self.assertEqual(verify.repository_of(written), expected, written)

    def test_trust_entries_must_say_how_to_verify_or_why_not(self):
        for bad in ('[[signed]]\nrepository = "a/*"\nmethod = "github"\n',
                    '[[signed]]\nrepository = "a/*"\nmethod = "cosign"\nissuer = "x"\n',
                    '[[signed]]\nrepository = "a/*"\nmethod = "notary"\n',
                    '[[unsigned]]\nrepository = "a/*"\n'):
            path = self.root / "bad.toml"
            path.write_text(bad)
            with self.assertRaises(ValueError, msg=bad):
                verify.load_trust(path)

    # --- Dockerfile parsing ---------------------------------------------------------

    def test_copy_from_an_image_is_checked_but_build_stages_are_not(self):
        self.tools.good.add(f"ghcr.io/example/tool:2{DIGEST}")
        results = self.check({"Dockerfile": f"""\
            FROM node:24{DIGEST} AS build
            FROM build AS test
            COPY --from=ghcr.io/example/tool:2{DIGEST} /tool /usr/bin/tool
            COPY --from=build /app /app
            COPY --from=0 /app /app2
            FROM scratch
        """})
        self.assertEqual(self.statuses(results), [(1, "unsigned", "ok"), (3, "verified", "ok")])

    def test_arg_defaults_are_resolved_and_an_arg_without_one_warns(self):
        results = self.check({"Dockerfile": f"""\
            ARG BASE=quay.io/someone/thing:1{DIGEST}
            ARG RUNTIME
            FROM $BASE
            FROM ${{RUNTIME}}
        """})
        self.assertEqual(self.statuses(results), [(3, "unknown", "fail"), (4, "unresolved", "warn")])

    def test_marker_allows_one_reference_on_the_line_or_its_arg(self):
        results = self.check({"Dockerfile": f"""\
            # supply-chain-policy: allow-unverified-image vendor image, unsigned upstream
            FROM quay.io/someone/thing:1{DIGEST}
            # supply-chain-policy: allow-unverified-image tracked in T-1
            ARG OTHER=quay.io/someone/other:1{DIGEST}

            FROM $OTHER
            FROM quay.io/someone/third:1{DIGEST}
        """})
        self.assertEqual(self.statuses(results),
                         [(2, "allowed", "ok"), (6, "allowed", "ok"), (7, "unknown", "fail")])

    # --- what is checked --------------------------------------------------------------

    def test_with_a_base_ref_only_new_references_are_checked(self):
        repo = self.root / "repo"
        self.write({"Dockerfile": f"FROM quay.io/someone/old:1{DIGEST}\n"})
        for command in (["init", "-q"], ["add", "."],
                        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
            subprocess.run(["git", "-C", str(repo), *command], check=True)
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        (repo / "Dockerfile").write_text(f"FROM quay.io/someone/old:1{DIGEST}\nFROM quay.io/someone/new:1{DIGEST}\n")
        (repo / "worker.Dockerfile").write_text(f"FROM quay.io/someone/old:1{DIGEST}\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        results = self.check(base_ref=base)
        self.assertEqual([(r.path, r.image) for r in results],
                         [("Dockerfile", f"quay.io/someone/new:1{DIGEST}")])

    def test_each_reference_is_verified_once_and_failures_are_retried(self):
        self.tools.good.add(f"ghcr.io/example/app:1{DIGEST}")
        self.tools.flaky = 2
        results = self.check({"a/Dockerfile": f"FROM ghcr.io/example/app:1{DIGEST}\n",
                              "b/Dockerfile": f"FROM ghcr.io/example/app:1{DIGEST}\n"})
        self.assertEqual([r.status for r in results], ["verified", "verified"])
        self.assertEqual(len(self.tools.commands), 3)
        self.assertEqual(self.sleeps, list(verify.RETRY_DELAYS))

    def test_the_shipped_trust_list_loads(self):
        rules = verify.load_trust(verify.TRUST_FILE)
        self.assertTrue(any(rule.signed for rule in rules))


if __name__ == "__main__":
    unittest.main()
