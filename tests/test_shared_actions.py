import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def action(name):
    # Ruby's standard YAML library avoids installing dependencies in credentialed jobs.
    return json.loads(subprocess.check_output([
        "ruby", "-ryaml", "-rjson", "-e",
        "puts JSON.generate(YAML.load_file(ARGV[0]))",
        str(ROOT / "actions" / name / "action.yml"),
    ], text=True))


class AuthTests(unittest.TestCase):
    def test_google_token_output_and_cleanup(self):
        cfg = action("google-auth")
        auth = cfg["runs"]["steps"][0]
        self.assertEqual(auth["with"]["token_format"], "${{ inputs.token_format }}")
        self.assertEqual(cfg["outputs"]["access_token"]["value"], "${{ steps.auth.outputs.access_token }}")
        for key in ("create_credentials_file", "export_environment_variables", "cleanup_credentials"):
            self.assertIs(auth["with"][key], True)
        self.assertNotIn("credentials_json", cfg["inputs"])

    def test_app_scope_and_revocation_are_preserved(self):
        cfg = action("github-app-token")
        step = cfg["runs"]["steps"][0]
        for key in cfg["inputs"]:
            self.assertEqual(step["with"][key], "${{ inputs." + key + " }}")
        self.assertIs(step["with"]["skip-token-revoke"], False)
        self.assertEqual(cfg["outputs"]["token"]["value"], "${{ steps.token.outputs.token }}")
        for key in ("owner", "repositories", "permission-actions", "permission-contents", "permission-issues", "permission-pull-requests"):
            self.assertEqual(cfg["inputs"][key]["default"], "")

    def test_actions_are_self_contained_and_dependencies_are_pinned(self):
        for name in ("google-auth", "github-app-token", "vercel-deploy"):
            cfg = action(name)
            for step in cfg["runs"]["steps"]:
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^[^@]+@[0-9a-f]{40}$")
                    self.assertFalse(step["uses"].startswith("./"))
                if "run" in step:
                    self.assertNotIn("${{ inputs.", step["run"])

    def test_registry_is_one_quoted_argument(self):
        cfg = action("google-auth")
        setup = cfg["runs"]["steps"][1]
        self.assertEqual(setup["if"], "inputs.setup_gcloud == 'true' || inputs.registry != ''")
        script = cfg["runs"]["steps"][2]["run"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stub = directory / "gcloud"
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
            stub.chmod(0o755)
            capture = directory / "args"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], CAPTURE=str(capture))
            env["REGISTRY"] = "europe-west1-docker.pkg.dev"
            subprocess.run(["bash", "-e", "-c", script], env=env, check=True, capture_output=True)
            self.assertEqual(capture.read_text().splitlines(), ["auth", "configure-docker", env["REGISTRY"], "--quiet"])
            capture.unlink()
            for value in ("--malicious", "registry; echo unsafe", "registry\nother"):
                env["REGISTRY"] = value
                result = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(capture.exists())


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
