"""Run attest-provenance.yml's subject check, the script itself out of the workflow."""

from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/attest-provenance.yml"
DIGEST_A, DIGEST_B = "a" * 64, "b" * 64


def check_script():
    source = WORKFLOW.read_text()
    step = source.split("      - name: Check the subjects\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class SubjectTests(unittest.TestCase):
    def check(self, subjects):
        with tempfile.TemporaryDirectory() as directory:
            subjects_file = Path(directory) / "subjects.txt"
            output = Path(directory) / "output"
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-c", check_script()],
                env={**os.environ, "SUBJECTS": subjects, "SUBJECTS_FILE": str(subjects_file),
                     "GITHUB_OUTPUT": str(output)},
                capture_output=True, text=True, timeout=10,
            )
            lines = subjects_file.read_text().splitlines() if result.returncode == 0 else None
            return result, lines

    def test_images_and_files_become_checksum_lines(self):
        result, lines = self.check(
            f"docker.io/archestra/platform@sha256:{DIGEST_A}\n"
            f"\n"
            f"{DIGEST_B}  archestra-platform-1.5.0.tgz\r\n"
            f"{DIGEST_A} *terraform-provider-archestra_3.2.1_linux_amd64.zip\n"
            f"{DIGEST_B}  ghcr.io/archestra-ai/appa-runtime\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(lines, [
            f"{DIGEST_A}  docker.io/archestra/platform",
            f"{DIGEST_B}  archestra-platform-1.5.0.tgz",
            f"{DIGEST_A}  terraform-provider-archestra_3.2.1_linux_amd64.zip",
            f"{DIGEST_B}  ghcr.io/archestra-ai/appa-runtime",
        ])

    def test_an_image_repository_may_hold_several_digests(self):
        # The docker run image and its -slim variant: two tags of one repository.
        result, lines = self.check(
            f"docker.io/archestra/platform@sha256:{DIGEST_A}\n"
            f"docker.io/archestra/platform@sha256:{DIGEST_B}\n"
            f"{DIGEST_A}  ghcr.io/archestra-ai/appa-runtime\n"
            f"{DIGEST_B}  ghcr.io/archestra-ai/appa-runtime\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(lines), 4)

    def test_anything_else_attests_nothing(self):
        for subjects in (
            "",
            "\n  \n",
            "docker.io/archestra/platform:1.5.0",
            f"docker.io/archestra/platform@sha256:{DIGEST_A[:-1]}",
            f"Docker.io/archestra/platform@sha256:{DIGEST_A}",
            f"{DIGEST_A}  ../escape.tgz",
            f"{DIGEST_A}  /absolute.tgz",
            f"{DIGEST_A}  ghcr.io/archestra-ai/appa-runtime:v1",
            f"{DIGEST_A}  two words.tgz",
            f"{DIGEST_A}  chart.tgz\n{DIGEST_B}  chart.tgz",
            f"{DIGEST_A}  dist/chart.tgz\n{DIGEST_B}  dist/chart.tgz",
            f"docker.io/archestra/platform@sha256:{DIGEST_A}\ndocker.io/archestra/platform@sha256:{DIGEST_A}",
            f"{DIGEST_A}  ghcr.io/archestra-ai/appa-runtime\nghcr.io/archestra-ai/appa-runtime@sha256:{DIGEST_A}",
        ):
            with self.subTest(subjects=subjects):
                result, _ = self.check(subjects)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::", result.stdout)


if __name__ == "__main__":
    unittest.main()
