import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "check_supply_chain_policy", Path(__file__).with_name("check_supply_chain_policy.py")
)
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy  # dataclasses resolve annotations through it
SPEC.loader.exec_module(policy)

WORKFLOW = """
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

ACTIONS_UPDATES = """
  - package-ecosystem: github-actions
    directory: /
    schedule: {interval: weekly}
    cooldown: {default-days: 7}
"""

PNPM_SETTINGS = "minimumReleaseAge: 10080\nignoreScripts: true\n"


def dependabot(*entries):
    return "version: 2\nupdates:\n" + "".join(entries)


def entry(ecosystem, directory="/", cooldown="{default-days: 7}"):
    lines = [
        f"  - package-ecosystem: {ecosystem}",
        f"    directory: {directory}",
        "    schedule: {interval: weekly}",
    ]
    if cooldown is not None:
        lines.append(f"    cooldown: {cooldown}")
    return "\n".join(lines) + "\n"


class PolicyTest(unittest.TestCase):
    def check(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, content in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(content))
            return policy.check_repository(root)

    def failures(self, files):
        return [f for f in self.check(files) if f.level == policy.FAIL]

    def warnings(self, files):
        return [f for f in self.check(files) if f.level == policy.WARN]

    def assertFinding(self, findings, path, text):
        matches = [f for f in findings if f.path == path and text in f.message]
        self.assertTrue(matches, f"no finding at {path} containing {text!r} in {findings}")


class DependabotTests(PolicyTest):
    def test_repository_without_dependencies_passes(self):
        self.assertEqual(self.check({"README.md": "docs only"}), [])

    def test_workflows_require_a_dependabot_config(self):
        failures = self.failures({".github/workflows/ci.yml": WORKFLOW})
        self.assertFinding(failures, ".github/dependabot.yml", "github-actions")

    def test_compliant_config_passes(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/dependabot.yml": dependabot(ACTIONS_UPDATES),
        }
        self.assertEqual(self.check(files), [])

    def test_every_entry_needs_a_seven_day_cooldown(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            "Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
            "Cargo.lock": "version = 4\n",
            ".github/dependabot.yml": dependabot(
                entry("github-actions", cooldown=None),
                entry("cargo", cooldown="{default-days: 3}"),
            ),
        }
        failures = self.failures(files)
        self.assertFinding(failures, ".github/dependabot.yml", "updates[0] (github-actions /) has no `cooldown`")
        self.assertFinding(failures, ".github/dependabot.yml", "`cooldown.default-days: 3`")

    def test_semver_overrides_must_also_be_seven_days(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/dependabot.yml": dependabot(
                entry("github-actions", cooldown="{default-days: 7, semver-major-days: 30, semver-patch-days: 1}")
            ),
        }
        failures = self.failures(files)
        self.assertEqual(len(failures), 1)
        self.assertFinding(failures, ".github/dependabot.yml", "`cooldown.semver-patch-days: 1`")

    def test_cooldown_may_exclude_but_not_narrow_to_a_subset(self):
        narrowed = {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/dependabot.yml": dependabot(
                entry("github-actions", cooldown="{default-days: 7, include: [actions/*]}")
            ),
        }
        self.assertFinding(self.failures(narrowed), ".github/dependabot.yml", "cooldown.include")
        excluded = dict(narrowed)
        excluded[".github/dependabot.yml"] = dependabot(
            entry("github-actions", cooldown="{default-days: 7, exclude: [actions/checkout]}")
        )
        self.assertEqual(self.check(excluded), [])

    def test_missing_package_manager_ecosystem_fails(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            "go.mod": "module example.com/a\n\nrequire example.com/b v1.0.0\n",
            ".github/dependabot.yml": dependabot(ACTIONS_UPDATES),
        }
        self.assertFinding(self.failures(files), ".github/dependabot.yml", "No `updates` entry for gomod")

    def test_missing_infrastructure_ecosystem_only_warns(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            "Dockerfile": "FROM scratch\n",
            "infra/main.tf": "terraform {}\n",
            ".github/dependabot.yml": dependabot(ACTIONS_UPDATES),
        }
        self.assertEqual(self.failures(files), [])
        warnings = self.warnings(files)
        self.assertFinding(warnings, ".github/dependabot.yml", "No `updates` entry for docker")
        self.assertFinding(warnings, ".github/dependabot.yml", "No `updates` entry for terraform")

    def test_repository_with_only_warn_level_ecosystems_only_warns(self):
        findings = self.check({"requirements.txt": "requests==2.32.0\n"})
        self.assertEqual([f.level for f in findings], [policy.WARN])

    def test_either_python_ecosystem_covers_python(self):
        files = {
            "requirements.txt": "requests==2.32.0\n",
            ".github/dependabot.yml": dependabot(entry("pip")),
        }
        self.assertEqual(self.check(files), [])

    def test_uncovered_composite_action_directory_warns(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/actions/setup/action.yml": "runs: {using: composite, steps: []}\n",
            "actions/one/action.yml": "runs: {using: composite, steps: []}\n",
            ".github/dependabot.yml": dependabot(
                "  - package-ecosystem: github-actions\n"
                "    directories: [/, /actions/*]\n"
                "    schedule: {interval: weekly}\n"
                "    cooldown: {default-days: 7}\n"
            ),
        }
        warnings = self.warnings(files)
        self.assertEqual(len(warnings), 1)
        self.assertFinding(warnings, ".github/dependabot.yml", "/.github/actions/setup")

    def test_workspace_members_need_no_entry_of_their_own(self):
        files = {
            "pnpm-workspace.yaml": "packages: [apps/*]\n" + PNPM_SETTINGS,
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "package.json": '{"devDependencies": {"typescript": "5.0.0"}}',
            "apps/web/package.json": '{"dependencies": {"react": "19.0.0"}}',
            ".github/dependabot.yml": dependabot(entry("npm")),
        }
        self.assertEqual(self.check(files), [])

    def test_package_json_without_dependencies_is_not_npm(self):
        self.assertEqual(self.check({"package.json": '{"name": "tool", "scripts": {}}'}), [])

    def test_unparseable_config_fails(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/dependabot.yml": "updates: [\n",
        }
        self.assertFinding(self.failures(files), ".github/dependabot.yml", "Cannot parse")

    def test_vendored_and_fixture_manifests_are_ignored(self):
        files = {
            "vendor/lib/Cargo.toml": '[package]\nname = "v"\n',
            "tests/fixtures/app/package.json": '{"dependencies": {"a": "1.0.0"}}',
            "tests/fixtures/app/pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        }
        self.assertEqual(self.check(files), [])


class PnpmTests(PolicyTest):
    def files(self, workspace, **extra):
        return {
            "pnpm-workspace.yaml": workspace,
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "package.json": '{"dependencies": {"a": "1.0.0"}}',
            ".github/dependabot.yml": dependabot(entry("npm")),
            **extra,
        }

    def test_required_settings_pass_with_exclusions(self):
        workspace = PNPM_SETTINGS + "minimumReleaseAgeExclude:\n  - handlebars@4.7.10\n"
        self.assertEqual(self.check(self.files(workspace)), [])

    def test_short_release_age_fails(self):
        failures = self.failures(self.files("minimumReleaseAge: 1440\nignoreScripts: true\n"))
        self.assertFinding(failures, "pnpm-workspace.yaml", "`minimumReleaseAge` is `1440`")

    def test_lifecycle_scripts_must_be_disabled(self):
        failures = self.failures(self.files("minimumReleaseAge: 10080\nignoreScripts: false\n"))
        self.assertFinding(failures, "pnpm-workspace.yaml", "`ignoreScripts` is `false`")

    def test_lockfile_without_workspace_file_fails(self):
        files = self.files("")
        del files["pnpm-workspace.yaml"]
        failures = self.failures(files)
        self.assertEqual(len(failures), 1)
        self.assertFinding(failures, "pnpm-workspace.yaml", "has no pnpm-workspace.yaml")

    def test_pnpm_too_old_for_release_age_fails(self):
        files = self.files(PNPM_SETTINGS)
        files["package.json"] = '{"packageManager": "pnpm@10.14.0+sha512.abc", "dependencies": {"a": "1"}}'
        self.assertFinding(self.failures(files), "package.json", "pnpm 10.14.0")
        files["package.json"] = '{"packageManager": "pnpm@10.16.0", "dependencies": {"a": "1"}}'
        self.assertEqual(self.check(files), [])

    def test_package_manager_field_without_dependencies_is_ignored(self):
        files = {"package.json": '{"name": "versioned", "packageManager": "pnpm@10.14.0"}'}
        self.assertEqual(self.check(files), [])

    def test_nested_workspaces_are_checked_separately(self):
        files = self.files(PNPM_SETTINGS)
        files["images/agent/pnpm-workspace.yaml"] = "ignoreScripts: true\n"
        files["images/agent/pnpm-lock.yaml"] = "lockfileVersion: '9.0'\n"
        failures = self.failures(files)
        self.assertEqual(len(failures), 1)
        self.assertFinding(failures, "images/agent/pnpm-workspace.yaml", "minimumReleaseAge")

    def test_pnpm10_npmrc_settings_pass_with_a_warning(self):
        files = self.files("")
        del files["pnpm-workspace.yaml"]
        files[".npmrc"] = "minimum-release-age=10080\nignore-scripts=true\n"
        self.assertEqual(self.failures(files), [])
        self.assertFinding(self.warnings(files), ".npmrc", "ignored by pnpm 11")


class OtherJavaScriptManagerTests(PolicyTest):
    def test_npm_project_without_age_gate_warns(self):
        files = {
            "package-lock.json": "{}",
            "package.json": '{"dependencies": {"a": "1.0.0"}}',
            ".github/dependabot.yml": dependabot(entry("npm")),
        }
        self.assertFinding(self.warnings(files), ".npmrc", "`min-release-age` is unset")
        files[".npmrc"] = "min-release-age=7\nignore-scripts=true\n"
        self.assertEqual(self.check(files), [])

    def test_bun_release_age_is_in_seconds(self):
        files = {
            "bun.lock": "{}",
            "package.json": '{"dependencies": {"a": "1.0.0"}}',
            ".github/dependabot.yml": dependabot(entry("bun")),
            "bunfig.toml": "[install]\nminimumReleaseAge = 10080\n",
        }
        self.assertFinding(self.warnings(files), "bunfig.toml", "`10080`")
        files["bunfig.toml"] = "[install]\nminimumReleaseAge = 604800\n"
        self.assertEqual(self.check(files), [])

    def test_bun_needs_its_own_dependabot_ecosystem(self):
        files = {
            "bun.lock": "{}",
            "package.json": '{"dependencies": {"a": "1.0.0"}}',
            "bunfig.toml": "[install]\nminimumReleaseAge = 604800\n",
            ".github/dependabot.yml": dependabot(entry("npm")),
        }
        self.assertFinding(self.failures(files), ".github/dependabot.yml", "No `updates` entry for bun")

    def test_yarn_age_gate(self):
        files = {
            "yarn.lock": "",
            "package.json": '{"dependencies": {"a": "1.0.0"}}',
            ".github/dependabot.yml": dependabot(entry("npm")),
        }
        self.assertFinding(self.warnings(files), "yarn.lock", "Yarn Classic")
        files[".yarnrc.yml"] = "npmMinimalAgeGate: 7d\n"
        self.assertEqual(self.check(files), [])


class CargoTests(PolicyTest):
    WORKSPACE = '[workspace]\nmembers = ["crates/*"]\nexclude = ["tools/standalone"]\n'
    PACKAGE = '[package]\nname = "a"\nversion = "0.1.0"\n'

    def files(self, **extra):
        return {
            "Cargo.toml": self.WORKSPACE,
            "Cargo.lock": "version = 4\n",
            "crates/a/Cargo.toml": self.PACKAGE,
            ".github/dependabot.yml": dependabot(entry("cargo")),
            **extra,
        }

    def test_workspace_members_share_the_root_lockfile(self):
        self.assertEqual(self.check(self.files()), [])

    def test_workspace_without_lockfile_fails(self):
        files = self.files()
        del files["Cargo.lock"]
        self.assertFinding(self.failures(files), "Cargo.lock", "has no committed Cargo.lock")

    def test_excluded_and_standalone_packages_need_their_own_lockfile(self):
        files = self.files(**{"tools/standalone/Cargo.toml": self.PACKAGE})
        self.assertFinding(self.failures(files), "tools/standalone/Cargo.lock", "Cargo package")
        files["tools/standalone/Cargo.lock"] = "version = 4\n"
        self.assertEqual(self.failures(files), [])

    def workflow(self, run):
        return (
            "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: |\n" + textwrap.indent(textwrap.dedent(run), " " * 10)
        )

    def ci(self, run):
        return self.files(
            **{
                ".github/workflows/ci.yml": self.workflow(run),
                ".github/dependabot.yml": dependabot(entry("cargo"), ACTIONS_UPDATES),
            }
        )

    def test_cargo_install_without_locked_fails(self):
        failures = self.failures(self.ci("cargo install cargo-nextest --version 0.9.100\n"))
        self.assertFinding(failures, ".github/workflows/ci.yml", "cargo install cargo-nextest")

    def test_cargo_build_without_locked_warns(self):
        files = self.ci("cargo build --release && cargo +nightly test -p a\n")
        self.assertEqual(self.failures(files), [])
        self.assertEqual(len(self.warnings(files)), 2)

    def test_locked_and_frozen_commands_pass(self):
        run = """
        cargo test --workspace \\
          --locked
        cargo install --frozen --path tools/x
        cargo fmt --check
        # cargo build is mentioned in a comment
        """
        self.assertEqual(self.check(self.ci(run)), [])

    def test_folded_scalar_commands_are_joined(self):
        workflow = (
            "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: >-\n          cargo test -p a\n          --locked\n"
        )
        files = self.ci("echo\n")
        files[".github/workflows/ci.yml"] = workflow
        self.assertEqual(self.check(files), [])


class UvTests(PolicyTest):
    def files(self, pyproject):
        return {
            "uv.lock": "version = 1\n",
            "pyproject.toml": pyproject,
            ".github/dependabot.yml": dependabot(entry("uv")),
        }

    def test_missing_exclude_newer_warns(self):
        warnings = self.warnings(self.files('[project]\nname = "a"\n'))
        self.assertFinding(warnings, "pyproject.toml", "no `exclude-newer`")

    def test_relative_exclude_newer_of_seven_days_passes(self):
        for value in ("7 days", "1 week", "P7D"):
            with self.subTest(value=value):
                files = self.files(f'[tool.uv]\nexclude-newer = "{value}"\n')
                self.assertEqual(self.check(files), [])

    def test_short_exclude_newer_warns(self):
        warnings = self.warnings(self.files('[tool.uv]\nexclude-newer = "24 hours"\n'))
        self.assertFinding(warnings, "pyproject.toml", "`exclude-newer` is `24 hours`")

    def test_uv_toml_is_read(self):
        files = self.files('[project]\nname = "a"\n')
        files["uv.toml"] = 'exclude-newer = "2026-01-01T00:00:00Z"\n'
        self.assertEqual(self.check(files), [])


class DurationTests(unittest.TestCase):
    def test_units(self):
        day = 86400
        cases = {
            ("7d", 60): 7 * day,
            ("1w", 60): 7 * day,
            ("2 weeks", 1): 14 * day,
            ("PT168H", None): 7 * day,
            (10080, 60): 7 * day,
            ("10080", 60): 7 * day,
            ("2026-01-01T00:00:00Z", None): None,
            (True, 1): None,
        }
        for (value, unit), expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(policy.parse_duration(value, default_unit=unit), expected)


class MainTests(PolicyTest):
    def run_main(self, files):
        with tempfile.TemporaryDirectory() as directory:
            for path, content in files.items():
                target = Path(directory, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            output = io.StringIO()
            # Plain-text output, as outside GitHub Actions.
            environment = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
            with mock.patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(output):
                code = policy.main([directory])
            return code, output.getvalue()

    def test_failures_exit_nonzero_and_name_the_fix(self):
        code, output = self.run_main({".github/workflows/ci.yml": WORKFLOW})
        self.assertEqual(code, 1)
        self.assertIn("fix: Add .github/dependabot.yml", output)

    def test_warnings_alone_pass(self):
        code, output = self.run_main({"requirements.txt": "requests==2.32.0\n"})
        self.assertEqual(code, 0)
        self.assertIn("Passed with warnings.", output)


if __name__ == "__main__":
    unittest.main()
