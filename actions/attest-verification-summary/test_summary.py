import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("summary", Path(__file__).with_name("summary.py"))
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

# `gh attestation verify --format json` for docker.io/archestra/platform:1.4.37's build provenance.
VERIFIED = json.loads(Path(__file__).with_name("testdata").joinpath("verify.json").read_text())
DIGEST = "46de5d667fa9b89ecc693516efdf1003452a95e9696159fb9262a8754f727f9c"
SIGNER = "archestra-ai/.github/.github/workflows/attest-provenance.yml"


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def run_summary(self, verified, repository="archestra-ai/archestra", signer=SIGNER):
        path = self.root / "verified.json"
        path.write_text(json.dumps(verified))
        predicate = self.root / "summary.json"
        with contextlib.redirect_stdout(io.StringIO()):
            summary.main([str(path), str(predicate), "--image", f"ghcr.io/archestra-ai/ci/platform@sha256:{DIGEST}",
                          "--subject-name", "docker.io/archestra/platform", "--repository", repository,
                          "--signer-workflow", signer, "--time", "2026-11-21T00:00:00Z"])
        return json.loads(predicate.read_text())

    def test_provenance_from_the_shared_signer_passes_at_build_level_3(self):
        predicate = self.run_summary(VERIFIED)
        self.assertEqual(predicate["verificationResult"], "PASSED")
        self.assertEqual(predicate["verifiedLevels"], ["SLSA_BUILD_LEVEL_3"])
        self.assertEqual(predicate["resourceUri"],
                         f"pkg:oci/platform@sha256%3A{DIGEST}?repository_url=docker.io/archestra/platform")
        self.assertEqual(predicate["inputAttestations"][0]["uri"],
                         "https://github.com/archestra-ai/archestra/actions/runs/44756315791/attempts/1")
        self.assertRegex(predicate["inputAttestations"][0]["digest"]["sha256"], r"^[0-9a-f]{64}$")

    def test_provenance_outside_the_policy_is_refused(self):
        def changed(change):
            verified = copy.deepcopy(VERIFIED)
            change(verified[0])
            return verified

        cases = {
            "another repository": (VERIFIED, {"repository": "archestra-ai/OpenAPPA"}),
            "another signer": (VERIFIED, {"signer": "archestra-ai/archestra/.github/workflows/release-please.yml"}),
            "a self-hosted runner": (changed(lambda r: r["verificationResult"]["signature"]["certificate"]
                                             .update(runnerEnvironment="self-hosted")), {}),
            "no transparency log entry": (changed(lambda r: r["attestation"]["bundle"]["verificationMaterial"]
                                                  .update(tlogEntries=[])), {}),
            # One provenance names every image of its run; none of them is this digest.
            "another digest": (changed(lambda r: [subject["digest"].update(sha256="b" * 64)
                                                  for subject in r["verificationResult"]["statement"]["subject"]
                                                  if subject["digest"]["sha256"] == DIGEST]), {}),
            "nothing verified": ([], {}),
        }
        for name, (verified, overrides) in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.run_summary(verified, **overrides)


if __name__ == "__main__":
    unittest.main()
