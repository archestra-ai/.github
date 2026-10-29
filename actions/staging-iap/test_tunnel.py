import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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

    def test_cleanup_only_signals_its_own_tunnel_and_never_uses_sudo(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "pid").write_text("1234")
            with patch.object(tunnel.subprocess, "run") as run, patch.object(tunnel.os, "killpg") as kill:
                run.return_value.stdout = "an-unrelated-process"
                tunnel.stop(state)
                kill.assert_not_called()
                self.assertFalse((state / "pid").exists())
                # Ownership is checked with ps alone: no hosts entry or NAT rule exists to remove.
                self.assertEqual([call.args[0][:3] for call in run.call_args_list], [("ps", "-p", "1234")])

    def test_start_health_checks_the_tunnel_directly_and_leaves_the_runner_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            process = MagicMock(pid=4321)
            process.poll.return_value = None
            with patch.object(tunnel.subprocess, "Popen", return_value=process) as popen, \
                    patch.object(tunnel.subprocess, "run") as run, \
                    patch.object(tunnel.socket, "socket", MagicMock()), \
                    patch.object(tunnel.socket, "create_connection", MagicMock()), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                tunnel.start(state)
                self.assertIn(f"--local-host-port=127.0.0.1:{tunnel.PORT}", popen.call_args.args[0])
                self.assertEqual((state / "pid").read_text(), "4321")
                # Exactly one command after the tunnel is up: the curl health check that
                # dials the local port with the real hostname for SNI and verification.
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual([command[0] for command in commands], ["curl"])
                self.assertEqual(commands[0][-3:], ("--connect-to", f"{tunnel.HOST}:443:127.0.0.1:{tunnel.PORT}",
                                                    f"https://{tunnel.HOST}/auth/sign-in"))
                self.assertEqual(output.getvalue(),
                                 "Private staging HTTPS verified through IAP; certificate checks remain enabled.\n")
                with self.assertRaises(RuntimeError):
                    tunnel.start(state)
                self.assertEqual([command[0] for command in commands], ["curl"])


if __name__ == "__main__":
    unittest.main()
