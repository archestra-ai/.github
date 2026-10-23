import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class VercelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.capture = self.directory / "args.json"
        self.output = self.directory / "output"
        stub = self.directory / "npx"
        stub.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["CAPTURE"]).write_text(json.dumps({"args":sys.argv[1:],"org":os.environ["VERCEL_ORG_ID"],"project":os.environ["VERCEL_PROJECT_ID"]}))
print("Inspect: https://inspect.vercel.app")
print("Production: https://production.vercel.app")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
''')
        stub.chmod(0o755)
        self.env = dict(os.environ, PATH=self.tmp.name + os.pathsep + os.environ["PATH"],
                        CAPTURE=str(self.capture), GITHUB_OUTPUT=str(self.output),
                        VERCEL_TOKEN="fake-test-token", VERCEL_ORG_ID="team-test",
                        VERCEL_PROJECT_ID="project-test", VERCEL_BUILD_ENV="{}")

    def run_deploy(self):
        return subprocess.run(["python3", str(ROOT / "actions/vercel-deploy/deploy.py")],
                              env=self.env, capture_output=True, text=True)

    def test_source_deploy_with_pinned_cli_and_url(self):
        self.assertEqual(self.run_deploy().returncode, 0)
        captured = json.loads(self.capture.read_text())
        self.assertEqual(captured, {"args": ["--yes", "vercel@59.15.1", "deploy", "--prod", "--yes", "--token=fake-test-token"], "org": "team-test", "project": "project-test"})
        self.assertEqual(self.output.read_text(), "deployment-url=https://production.vercel.app\n")

    def test_build_values_are_literal_not_shell_code(self):
        marker = self.directory / "injection"
        value = f"$(touch {marker}); literal with spaces"
        self.env["VERCEL_BUILD_ENV"] = json.dumps({"ARCHESTRA_WEBSITE_DOCS_REF": value})
        self.assertEqual(self.run_deploy().returncode, 0)
        args = json.loads(self.capture.read_text())["args"]
        self.assertIn("ARCHESTRA_WEBSITE_DOCS_REF=" + value, args)
        self.assertFalse(marker.exists())

    def test_cli_failure_does_not_emit_success_output(self):
        self.env["FAKE_EXIT"] = "7"
        self.assertEqual(self.run_deploy().returncode, 7)
        self.assertFalse(self.output.exists())

    def test_invalid_configuration_never_calls_cli(self):
        for value in ("[]", "invalid", '{"BAD-NAME":"value"}', '{"GOOD":4}'):
            self.env["VERCEL_BUILD_ENV"] = value
            self.assertNotEqual(self.run_deploy().returncode, 0)
            self.assertFalse(self.capture.exists())
        self.env["VERCEL_BUILD_ENV"] = "{}"
        for name in ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"):
            original = self.env[name]
            self.env[name] = ""
            self.assertNotEqual(self.run_deploy().returncode, 0)
            self.assertFalse(self.capture.exists())
            self.env[name] = original


if __name__ == "__main__":
    unittest.main()
