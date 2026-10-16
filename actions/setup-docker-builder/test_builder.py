import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


cache = module("cache_policy", "cache-policy.py")
store = module("native_store", "enable-containerd.py")


class CachePolicy(unittest.TestCase):
    def resolve(self, **kwargs):
        args = dict(scope="runtime-amd64", mode="max", write="auto", event="push",
                    ref="refs/heads/main", default_branch="main", driver="docker-container")
        return cache.policy(**(args | kwargs))

    def test_only_default_branch_pushes_and_manual_runs_warm_by_default(self):
        for event, ref, writer in [
            ("push", "refs/heads/main", True),
            ("workflow_dispatch", "refs/heads/main", True),
            ("push", "refs/heads/feature", False),
            ("push", "refs/tags/v1", False),
            ("pull_request", "refs/pull/10/merge", False),
            ("pull_request_target", "refs/heads/main", False),
            ("merge_group", "refs/heads/gh-readonly-queue/main/pr-10", False),
        ]:
            with self.subTest(event=event, ref=ref):
                result = self.resolve(event=event, ref=ref)
                self.assertEqual(bool(result["cache-to"]), writer)
                self.assertEqual(result["cache-write"], str(writer).lower())

    def test_custom_default_branch_and_explicit_reader_writer(self):
        self.assertTrue(self.resolve(ref="refs/heads/trunk", default_branch="trunk")["cache-to"])
        self.assertFalse(self.resolve(default_branch="")["cache-to"])
        self.assertFalse(self.resolve(write="false")["cache-to"])
        self.assertTrue(self.resolve(write="true", event="pull_request")["cache-to"])

    def test_runner_selects_cache_protocol_and_authentication(self):
        result = self.resolve()
        self.assertEqual(result["cache-from"], "type=gha,scope=runtime-amd64")
        self.assertEqual(result["cache-to"], "type=gha,scope=runtime-amd64,mode=max")
        for text in result.values():
            for forbidden in ("version=", "url=", "token=", "repository="):
                self.assertNotIn(forbidden, text)
        self.assertNotEqual(result["cache-from"], self.resolve(scope="runtime-arm64")["cache-from"])
        self.assertFalse(self.resolve(scope="")["cache-from"])
        self.assertFalse(self.resolve(scope="")["cache-to"])

    def test_invalid_specs_cannot_inject_additional_cache_parameters_or_outputs(self):
        for args in [dict(scope="image,version=2"), dict(scope="image\ncache-write=true"),
                     dict(mode="anything"), dict(write="yes"), dict(driver="remote")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.resolve(**args)


class NativeStore(unittest.TestCase):
    def test_preserves_registry_mirrors_dns_and_other_features(self):
        original = {"registry-mirrors": ["https://mirror.gcr.io"],
                    "dns-opts": ["attempts:3"], "features": {"buildkit": True},
                    "experimental": False}
        result = store.merged_config(original)
        self.assertEqual(result | {"features": original["features"]}, original)
        self.assertEqual(result["features"], {"buildkit": True, "containerd-snapshotter": True})
        self.assertEqual(original["features"], {"buildkit": True})

    def test_refuses_active_or_stopped_containers_before_mutating_config(self):
        for containers in ("active-container", "stopped-container"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "daemon.json"
                with patch.object(store, "command", side_effect=[
                    json.dumps({"ServerVersion": "28.0.4", "DriverStatus": []}), containers
                ]), patch.object(store.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "before creating"):
                        store.enable(path)
                    self.assertFalse(path.exists())
                    run.assert_not_called()

    def test_enabled_store_does_not_restart_or_modify_config(self):
        with patch.object(store, "command", return_value=json.dumps({
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]
        })), patch.object(store.subprocess, "run") as run:
            store.enable(Path("must-not-be-written/daemon.json"))
            run.assert_not_called()

    def test_restart_failure_restores_original_configuration_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.json"
            original = b'{"registry-mirrors":["https://mirror.gcr.io"]}\n'
            path.write_bytes(original)
            path.chmod(0o640)
            with patch.object(store, "command", side_effect=[
                json.dumps({"ServerVersion": "28.0.4", "DriverStatus": []}), ""
            ]), patch.object(store.subprocess, "run", side_effect=[
                None, subprocess.CalledProcessError(1, "systemctl"), None
            ]):
                with self.assertRaises(subprocess.CalledProcessError):
                    store.enable(path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["daemon.json"])


if __name__ == "__main__":
    unittest.main()
