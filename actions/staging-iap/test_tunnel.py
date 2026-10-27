import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("tunnel", Path(__file__).with_name("tunnel.py"))
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


class TunnelBoundaryTests(unittest.TestCase):
    def test_only_same_repository_organization_pull_requests_and_fixed_tls_gateway_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"pull_request": {
                "head": {"repo": {"id": 1020144870}}, "base": {"repo": {"id": 1020144870}}}}))
            environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux",
                           "GITHUB_REPOSITORY": "archestra-ai/archestra", "GITHUB_REPOSITORY_ID": "1020144870",
                           "GITHUB_REPOSITORY_OWNER_ID": "216022683",
                           "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event),
                           "GATEWAY_URL": "https://frontend.archestra.dev/v1/mcp/416c32d5-8c40-4bf3-8544-212c200e3ca2"}
            with patch.dict(os.environ, environment, clear=True):
                tunnel.check()
                # Another repository of the organization passes with its own same-repository pull request.
                website = {"GITHUB_REPOSITORY": "archestra-ai/website", "GITHUB_REPOSITORY_ID": "555"}
                event.write_text(json.dumps({"pull_request": {"head": {"repo": {"id": 555}},
                                                               "base": {"repo": {"id": 555}}}}))
                with patch.dict(os.environ, website):
                    tunnel.check()
                event.write_text(json.dumps({"pull_request": {
                    "head": {"repo": {"id": 1020144870}}, "base": {"repo": {"id": 1020144870}}}}))
                for key, value in [("GITHUB_REPOSITORY_OWNER_ID", "999"), ("GITHUB_REPOSITORY", "attacker/archestra"),
                                   ("GITHUB_REPOSITORY_ID", "123"), ("GITHUB_EVENT_NAME", "pull_request_target"),
                                   ("RUNNER_OS", "macOS"), ("GATEWAY_URL", "https://frontend.archestra.dev.evil.test/v1/mcp/a"),
                                   ("GATEWAY_URL", "http://frontend.archestra.dev/v1/mcp/416c32d5-8c40-4bf3-8544-212c200e3ca2"),
                                   ("GATEWAY_URL", environment["GATEWAY_URL"] + "?token=anything")]:
                    with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                        with self.assertRaises(RuntimeError):
                            tunnel.check()
                event.write_text(json.dumps({"pull_request": {"head": {"repo": {"id": 999}},
                                                               "base": {"repo": {"id": 1020144870}}}}))
                with self.assertRaises(RuntimeError):
                    tunnel.check()

    def test_cleanup_without_owned_state_does_not_change_the_runner(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tunnel.subprocess, "run") as run:
            tunnel.stop(Path(directory))
            run.assert_not_called()

    def test_failed_iap_start_removes_its_overrides_and_preserves_other_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "pid").write_text("1234")
            with patch.object(tunnel.subprocess, "run") as run, patch.object(tunnel.os, "killpg") as kill:
                run.return_value.stdout = "an-unrelated-process"
                tunnel.stop(state)
                kill.assert_not_called()
                self.assertFalse((state / "pid").exists())
                self.assertEqual(run.call_args_list[0].args[0][-len(tunnel.RULE):], tunnel.RULE)


if __name__ == "__main__":
    unittest.main()
