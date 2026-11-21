import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("plan", Path(__file__).with_name("plan.py"))
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

PLATFORM = {"key": "docker.io/archestra/platform", "dockerfile": "platform/Dockerfile", "context": "platform"}
SLIM = {**PLATFORM, "key": "docker.io/archestra/platform:slim", "target": "unified-slim"}
AGENT = {"key": "ghcr.io/archestra-ai/agent-codex", "dockerfile": "agent/Dockerfile", "context": ".", "target": "agent-codex"}
DEV = {"dockerfile": "dev/*/Dockerfile", "reason": "Local development images, never released."}


class Repo:
    """A Git repository whose commits the tests write."""

    def __init__(self, root):
        self.root = root
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files=None, delete=()):
        for path, content in (files or {}).items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content if isinstance(content, str) else json.dumps(content))
        for path in delete:
            (self.root / path).unlink()
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "--message", "change")
        return self.git("rev-parse", "HEAD")


class PlanTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Repo(Path(directory.name))

    def base(self, files):
        return self.repo.commit({"README.md": "fixture\n", **files})

    def plan(self, base):
        return plan.plan(self.repo.root, base)

    def keys(self, result):
        return [image["key"] for image in result["images"]]

    def manifest(self, images=(PLATFORM,), unreleased=()):
        return {"images": list(images), **({"unreleased": list(unreleased)} if unreleased else {})}

    def platform_repo(self, **extra):
        return self.base({"platform/Dockerfile": "FROM scratch\n", ".github/images.json": self.manifest(**extra)})

    def test_a_repository_without_dockerfiles_passes(self):
        base = self.base({})
        self.repo.commit({"src/main.py": "print()\n"})
        result = self.plan(base)
        self.assertEqual((result["images"], result["errors"]), ([], []))
        self.assertEqual(result["notes"], ["No Dockerfile; nothing to check."])

    def test_an_undeclared_dockerfile_passes_until_a_change_touches_it(self):
        base = self.base({"app/Dockerfile": "FROM scratch\n"})
        self.repo.commit({"src/main.py": "print()\n"})
        self.assertEqual(self.plan(base)["errors"], [])

        self.repo.commit({"app/Dockerfile": "FROM scratch\nUSER 1\n"})
        errors = self.plan(base)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("app/Dockerfile is not declared in .github/images.json", errors[0])

    def test_a_new_dockerfile_must_be_declared(self):
        base = self.platform_repo()
        self.repo.commit({"tools/build.Dockerfile": "FROM scratch\n", "tools/Dockerfile.dockerignore": "*\n"})
        errors = self.plan(base)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("tools/build.Dockerfile is not declared", errors[0])

    def test_only_images_whose_dockerfile_changed_are_checked(self):
        base = self.base({"platform/Dockerfile": "FROM scratch\n", "agent/Dockerfile": "FROM scratch AS agent-codex\n",
                          ".github/images.json": self.manifest([PLATFORM, SLIM, AGENT])})
        self.repo.commit({"platform/Dockerfile": "FROM scratch\nUSER 1\n"})
        result = self.plan(base)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.keys(result), ["docker.io/archestra/platform", "docker.io/archestra/platform:slim"])
        self.assertEqual(result["images"][1]["reasons"], ["its Dockerfile changed"])
        self.assertEqual(result["images"][1]["target"], "unified-slim")
        self.assertEqual(result["images"][0]["runner"], "ubuntu-latest")

    def test_a_change_outside_the_images_builds_nothing(self):
        base = self.platform_repo()
        self.repo.commit({"platform/src/app.ts": "export {}\n"})
        result = self.plan(base)
        self.assertEqual((result["images"], result["errors"]), ([], []))

    def test_a_dockerignore_change_checks_the_image(self):
        base = self.platform_repo()
        self.repo.commit({"platform/.dockerignore": "!.env\n"})
        self.assertEqual(self.plan(base)["images"][0]["reasons"], ["its .dockerignore changed"])

    def test_a_changed_or_new_entry_is_checked(self):
        base = self.base({"platform/Dockerfile": "FROM scratch\n", "agent/Dockerfile": "FROM scratch\n",
                          ".github/images.json": self.manifest([PLATFORM, AGENT])})
        self.repo.commit({".github/images.json": self.manifest(
            [{**PLATFORM, "build-args": {"VERSION": "1"}}, AGENT, SLIM])})
        result = self.plan(base)
        self.assertEqual(result["errors"], [])
        self.assertEqual({image["key"]: image["reasons"] for image in result["images"]}, {
            "docker.io/archestra/platform": ["its .github/images.json entry changed"],
            "docker.io/archestra/platform:slim": ["it is new in .github/images.json"],
        })

    def test_changing_the_list_requires_every_dockerfile_declared(self):
        base = self.base({"platform/Dockerfile": "FROM scratch\n", "agent/Dockerfile": "FROM scratch\n",
                          ".github/images.json": self.manifest([PLATFORM, AGENT])})
        self.repo.commit({".github/images.json": self.manifest([PLATFORM])})
        errors = self.plan(base)["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("agent/Dockerfile is not declared", errors[0])

    def test_a_waiver_change_checks_the_images_it_names(self):
        waiver = {"images": ["*/agent-*"], "check": "DKL-DI-0003", "reason": "Installs the apt package, no dist-upgrade."}
        base = self.base({"platform/Dockerfile": "FROM scratch\n", "agent/Dockerfile": "FROM scratch\n",
                          ".github/images.json": self.manifest([PLATFORM, AGENT]),
                          ".github/image-benchmark-waivers.json": {"waivers": []}})
        self.repo.commit({".github/image-benchmark-waivers.json": {"waivers": [waiver]}})
        result = self.plan(base)
        self.assertEqual(self.keys(result), ["ghcr.io/archestra-ai/agent-codex"])
        self.assertEqual(result["images"][0]["reasons"], ["its waivers changed"])

        self.repo.commit({".github/image-benchmark-waivers.json": {"waivers": [{**waiver, "images": ["nothing"]}]}})
        self.assertEqual(self.keys(self.plan(base)), [])

    def test_invalid_waivers_fail(self):
        base = self.platform_repo()
        self.repo.commit({".github/image-benchmark-waivers.json": {"waivers": [{"images": ["*"], "check": "CIS-DI-0001"}]}})
        result = self.plan(base)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("image-benchmark-waivers.json", result["errors"][0])

    def test_unreleased_dockerfiles_need_no_build(self):
        base = self.platform_repo(unreleased=[DEV])
        self.repo.commit({"dev/lite/Dockerfile": "FROM scratch\n"})
        result = self.plan(base)
        self.assertEqual((result["images"], result["errors"]), ([], []))

    def test_an_unreleased_entry_that_matches_nothing_fails(self):
        base = self.platform_repo()
        self.repo.commit({".github/images.json": self.manifest(unreleased=[DEV])})
        self.assertIn("unreleased dev/*/Dockerfile matches no Dockerfile", self.plan(base)["errors"][0])

    def test_a_dockerfile_cannot_be_both_an_image_and_unreleased(self):
        base = self.platform_repo()
        self.repo.commit({".github/images.json": self.manifest(
            unreleased=[{"dockerfile": "platform/Dockerfile", "reason": "Not really released."}])})
        self.assertIn("both images and unreleased", self.plan(base)["errors"][0])

    def test_an_entry_for_a_deleted_dockerfile_fails(self):
        base = self.platform_repo()
        self.repo.commit(delete=["platform/Dockerfile"])
        self.assertIn("platform/Dockerfile does not exist", self.plan(base)["errors"][0])

    def test_without_a_base_every_image_is_checked(self):
        self.base({"platform/Dockerfile": "FROM scratch\n", "agent/Dockerfile": "FROM scratch\n",
                   ".github/images.json": self.manifest([PLATFORM, AGENT])})
        self.assertEqual(self.keys(self.plan(None)), ["docker.io/archestra/platform", "ghcr.io/archestra-ai/agent-codex"])

    def test_invalid_lists_fail(self):
        cases = {
            "not valid JSON": "{",
            "must be an object": [],
            "must have key, dockerfile and context": {"images": [{"key": "x/y", "dockerfile": "platform/Dockerfile"}]},
            "only build-args, build-args-file, prepare, runner, target": {"images": [{**PLATFORM, "push": True}]},
            "build-args-file must be a path": {"images": [{**PLATFORM, "build-args-file": "/etc/args"}]},
            "key must be": {"images": [{**PLATFORM, "key": "Docker.io/Archestra"}]},
            "dockerfile must be a path in the repository": {"images": [{**PLATFORM, "dockerfile": "../Dockerfile"}]},
            "context must be a path": {"images": [{**PLATFORM, "context": "/abs"}]},
            "target must be": {"images": [{**PLATFORM, "target": "a b"}]},
            "build-args must map": {"images": [{**PLATFORM, "build-args": {"A": "x\ny"}}]},
            "prepare must be": {"images": [{**PLATFORM, "prepare": "make"}]},
            "runner must be a GitHub-hosted": {"images": [{**PLATFORM, "runner": "self-hosted"}]},
            "listed more than once": {"images": [PLATFORM, PLATFORM]},
            "exactly dockerfile and reason": {"images": [], "unreleased": [{"dockerfile": "x/Dockerfile"}]},
            "reason must say": {"images": [], "unreleased": [{"dockerfile": "x/Dockerfile", "reason": "no"}]},
        }
        base = self.platform_repo()
        for message, manifest in cases.items():
            with self.subTest(message):
                self.repo.commit({".github/images.json": manifest if isinstance(manifest, str) else json.dumps(manifest)})
                errors = self.plan(base)["errors"]
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(message, errors[0])

    def test_a_missing_context_fails_unless_prepare_makes_it(self):
        base = self.platform_repo()
        self.repo.commit({".github/images.json": self.manifest([{**PLATFORM, "context": "out/context"}])})
        self.assertIn("context out/context is not a directory", self.plan(base)["errors"][0])
        self.repo.commit({".github/images.json": self.manifest(
            [{**PLATFORM, "context": "out/context", "prepare": ["bash", "scripts/context.sh"]}])})
        self.assertEqual(self.plan(base)["errors"], [])

    def test_main_writes_the_matrix(self):
        base = self.platform_repo()
        self.repo.commit({"platform/Dockerfile": "FROM scratch\nUSER 1\n",
                          ".github/image-benchmark-waivers.json": {"waivers": []}})
        output, summary = self.repo.root / "output", self.repo.root / "summary.md"
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = plan.main(["plan", str(self.repo.root), "--base-ref", base, "--output", str(output),
                                "--summary", str(summary)])
        self.assertEqual(status, 0)
        self.assertIn("Check docker.io/archestra/platform (platform/Dockerfile): its Dockerfile changed", stdout.getvalue())
        lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(json.loads(lines["matrix"]), {"include": [{
            "build-args": {}, "build-args-file": "", "context": "platform", "dockerfile": "platform/Dockerfile",
            "hardened-bases": [], "key": "docker.io/archestra/platform", "prepare": [], "runner": "ubuntu-latest",
            "target": ""}]})
        self.assertEqual(lines["build"], "true")
        self.assertEqual(lines["waivers"], ".github/image-benchmark-waivers.json")
        self.assertIn("`docker.io/archestra/platform`", summary.read_text())

    def test_main_lists_the_hardened_bases_to_pull_first(self):
        hardened = "dhi.io/node:24-alpine3.23-dev@sha256:" + "a" * 64
        base = self.platform_repo()
        self.repo.commit({"platform/Dockerfile": (
            f"FROM node:24 AS build\nFROM --platform=$BUILDPLATFORM {hardened} AS runtime-base\n"
            f"from {hardened}\nFROM runtime-base\nFROM ${{BASE}}\n")})
        output = self.repo.root / "output"
        with contextlib.redirect_stdout(io.StringIO()):
            plan.main(["plan", str(self.repo.root), "--base-ref", base, "--output", str(output)])
        lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(json.loads(lines["matrix"])["include"][0]["hardened-bases"], [hardened])

    def test_main_fails_on_errors(self):
        base = self.base({})
        self.repo.commit({"Dockerfile": "FROM scratch\n"})
        output = self.repo.root / "output"
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = plan.main(["plan", str(self.repo.root), "--base-ref", base, "--output", str(output)])
        self.assertEqual(status, 1)
        self.assertIn("::error::Dockerfile is not declared", stdout.getvalue())
        self.assertFalse(output.exists())


class BuildTests(unittest.TestCase):
    def build(self, entry):
        runs = []
        with mock.patch.object(plan.subprocess, "run", side_effect=lambda command, **kwargs: runs.append((command, kwargs))), \
                mock.patch.dict(os.environ, {"IMAGE_ENTRY": json.dumps(entry)}), \
                contextlib.redirect_stdout(io.StringIO()):
            status = plan.main(["build", "/repo", "--tag", "image-benchmark/candidate:latest"])
        self.assertEqual(status, 0)
        return runs

    def test_it_builds_the_planned_image_for_amd64(self):
        entry = {**plan.normalized({**SLIM, "build-args": {"VERSION": "1.0", "A": "b c"}}), "reasons": ["x"]}
        [(command, kwargs)] = self.build(entry)
        self.assertEqual(command, [
            "docker", "build", "--platform", "linux/amd64", "--file", "/repo/platform/Dockerfile",
            "--tag", "image-benchmark/candidate:latest", "--target", "unified-slim",
            "--build-arg", "A=b c", "--build-arg", "VERSION=1.0", "/repo/platform"])
        self.assertTrue(kwargs["check"])

    def test_it_prepares_the_context_first(self):
        entry = plan.normalized({**PLATFORM, "context": "out", "prepare": ["bash", "scripts/context.sh"]})
        (prepare, prepare_kwargs), (build, _) = self.build(entry)
        self.assertEqual(prepare, ["bash", "scripts/context.sh"])
        self.assertEqual(str(prepare_kwargs["cwd"]), "/repo")
        self.assertEqual(build[-1], "/repo/out")

    def test_it_reads_build_args_the_prepare_command_wrote(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "args.env").write_text("# made by prepare\nVERSION=1.0\n\nNAME=a=b\n")
            entry = plan.normalized({**PLATFORM, "build-args-file": "args.env", "build-args": {"VERSION": "2.0"}})
            runs = []
            with mock.patch.object(plan.subprocess, "run", side_effect=lambda command, **kwargs: runs.append(command)), \
                    contextlib.redirect_stdout(io.StringIO()):
                plan.build(Path(directory), entry, "t")
            self.assertEqual(runs[0][runs[0].index("--tag") + 2:-1], ["--build-arg", "NAME=a=b", "--build-arg", "VERSION=2.0"])
            Path(directory, "args.env").write_text("not an argument\n")
            with self.assertRaisesRegex(ValueError, "args.env:1: expected NAME=value"), contextlib.redirect_stdout(io.StringIO()):
                plan.build(Path(directory), entry, "t")

    def test_it_checks_the_entry_again(self):
        entry = plan.normalized({**PLATFORM, "runner": "self-hosted"})
        with mock.patch.dict(os.environ, {"IMAGE_ENTRY": json.dumps(entry)}), self.assertRaises(ValueError):
            plan.main(["build", "/repo", "--tag", "t"])


if __name__ == "__main__":
    unittest.main()
