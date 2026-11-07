#!/usr/bin/env python3
"""Check a repository against the archestra-ai supply-chain policy.

Each rule applies only to the ecosystems a repository uses. A failure blocks
the pull request; a warning is reported and does not. See README.md beside
this file for the policy and how to fix each finding.

Requires Python 3.11+ and mikefarah yq v4 on PATH (preinstalled on
GitHub-hosted Ubuntu runners) to read YAML. Nothing else.
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

FAIL = "fail"
WARN = "warn"

MIN_COOLDOWN_DAYS = 7
MIN_RELEASE_AGE_MINUTES = 7 * 24 * 60  # 10080
MIN_AGE_SECONDS = MIN_RELEASE_AGE_MINUTES * 60
PNPM_MIN_RELEASE_AGE_VERSION = (10, 16, 0)
POLICY_DOC = "https://github.com/archestra-ai/.github/tree/main/supply-chain-policy"

# Dependabot ecosystems whose absence from dependabot.yml fails the check. The
# rest only warn: see "Ecosystem coverage" in README.md.
REQUIRED_ECOSYSTEMS = ("github-actions", "npm", "bun", "cargo", "gomod")


@dataclass(frozen=True)
class Finding:
    level: str
    path: str
    message: str
    fix: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        findings = check_repository(root)
    except ToolError as error:
        print(f"::error::{error}" if in_github_actions() else f"error: {error}")
        return 2
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        report(findings)
    return 1 if any(f.level == FAIL for f in findings) else 0


def check_repository(root: Path) -> list[Finding]:
    repo = Repository(root)
    ecosystems = detect_ecosystems(repo)
    findings: list[Finding] = []
    findings += check_dependabot(repo, ecosystems)
    findings += check_pnpm(repo)
    findings += check_npm(repo)
    findings += check_yarn(repo)
    findings += check_bun(repo)
    findings += check_cargo(repo)
    findings += check_uv(repo)
    return sorted(findings, key=lambda f: (f.level != FAIL, f.path, f.message))


# =============================================================================
# Dependabot
# =============================================================================


def check_dependabot(repo: Repository, ecosystems: dict[str, set[str]]) -> list[Finding]:
    config_path = next(
        (p for p in (".github/dependabot.yml", ".github/dependabot.yaml") if repo.has(p)),
        None,
    )
    required = sorted(e for e in ecosystems if e in REQUIRED_ECOSYSTEMS)
    if config_path is None:
        if not ecosystems:
            return []
        used = ", ".join(sorted(ecosystems))
        return [
            Finding(
                FAIL if required else WARN,
                ".github/dependabot.yml",
                f"Missing, but this repository uses {used}.",
                "Add .github/dependabot.yml with an `updates` entry per ecosystem, each "
                f"with `cooldown: {{default-days: {MIN_COOLDOWN_DAYS}}}`. "
                f"Starter config: {POLICY_DOC}#starter-configuration",
            )
        ]

    try:
        config = repo.yaml(config_path)
    except ParseError as error:
        return [Finding(FAIL, config_path, f"Cannot parse: {error}", "Fix the YAML syntax.")]
    updates = config.get("updates") if isinstance(config, dict) else None
    if not isinstance(updates, list):
        return [
            Finding(FAIL, config_path, "Has no `updates` list.", "Add an `updates` entry per ecosystem.")
        ]

    findings: list[Finding] = []
    covered: dict[str, list[str]] = {}
    for index, entry in enumerate(updates):
        if not isinstance(entry, dict):
            continue
        ecosystem = str(entry.get("package-ecosystem", "?"))
        where = f"updates[{index}] ({ecosystem} {describe_directories(entry)})"
        covered.setdefault(ecosystem, []).extend(entry_directories(entry))
        findings += check_cooldown(config_path, where, entry.get("cooldown"))

    for ecosystem in sorted(ecosystems):
        accepted = ACCEPTED_DEPENDABOT_ECOSYSTEMS.get(ecosystem, (ecosystem,))
        patterns = [d for key in accepted for d in covered.get(key, [])]
        dirs = sorted(ecosystems[ecosystem])
        if not any(key in covered for key in accepted):
            findings.append(
                Finding(
                    FAIL if ecosystem in REQUIRED_ECOSYSTEMS else WARN,
                    config_path,
                    f"No `updates` entry for {ecosystem} "
                    f"(found in {format_dirs(dirs)}).",
                    f"Add an entry with `package-ecosystem: {accepted[0]}`, the "
                    f"directories above and `cooldown: {{default-days: {MIN_COOLDOWN_DAYS}}}`.",
                )
            )
            continue
        if ecosystem not in DIRECTORY_SCOPED_ECOSYSTEMS:
            continue
        uncovered = [d for d in dirs if not directory_covered(d, patterns)]
        if uncovered:
            findings.append(
                Finding(
                    WARN,
                    config_path,
                    f"{ecosystem} in {format_dirs(uncovered)} is not under any "
                    f"{accepted[0]} `updates` entry's directories, so Dependabot never "
                    "updates it.",
                    "Add these directories to the entry (`directories:` accepts globs).",
                )
            )
    return findings


def check_cooldown(path: str, where: str, cooldown: object) -> list[Finding]:
    fix = f"Set `cooldown: {{default-days: {MIN_COOLDOWN_DAYS}}}` (or more) on this entry."
    if not isinstance(cooldown, dict):
        return [Finding(FAIL, path, f"{where} has no `cooldown`.", fix)]
    findings = []
    default = cooldown.get("default-days")
    if not is_int(default) or default < MIN_COOLDOWN_DAYS:
        findings.append(
            Finding(
                FAIL,
                path,
                f"{where} has `cooldown.default-days: {default}`; the minimum is "
                f"{MIN_COOLDOWN_DAYS}.",
                fix,
            )
        )
    for key in ("semver-major-days", "semver-minor-days", "semver-patch-days"):
        if key in cooldown and (not is_int(cooldown[key]) or cooldown[key] < MIN_COOLDOWN_DAYS):
            findings.append(
                Finding(
                    FAIL,
                    path,
                    f"{where} has `cooldown.{key}: {cooldown[key]}`; the minimum is "
                    f"{MIN_COOLDOWN_DAYS}.",
                    f"Raise `{key}` to {MIN_COOLDOWN_DAYS} or remove it.",
                )
            )
    include = cooldown.get("include")
    if include is not None and "*" not in (include if isinstance(include, list) else [include]):
        findings.append(
            Finding(
                FAIL,
                path,
                f"{where} limits the cooldown to `cooldown.include`, so every other "
                "dependency updates without one.",
                "Remove `include` and list exceptions under `cooldown.exclude` instead.",
            )
        )
    return findings


def entry_directories(entry: dict) -> list[str]:
    values = []
    if "directory" in entry:
        values.append(entry["directory"])
    if isinstance(entry.get("directories"), list):
        values.extend(entry["directories"])
    return [normalize_dir(str(v)) for v in values]


def describe_directories(entry: dict) -> str:
    dirs = entry_directories(entry)
    return ", ".join("/" + d for d in dirs) if dirs else "no directory"


def directory_covered(directory: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == directory or fnmatch.fnmatchcase(directory, pattern):
            return True
        if pattern.endswith("/**") and (directory + "/").startswith(pattern[:-2]):
            return True
        if pattern == "**":
            return True
    return False


def normalize_dir(value: str) -> str:
    value = value.strip().strip("/")
    while value.startswith("./"):
        value = value[2:]
    return "" if value in ("", ".") else value


# Dependabot `package-ecosystem` values that update each detected ecosystem.
ACCEPTED_DEPENDABOT_ECOSYSTEMS = {
    "python": ("uv", "pip"),
}

# Ecosystems whose updates entries must name the manifest's directory. Docker,
# Terraform and the like are reported by ecosystem only: their files are spread
# out and often maintained by hand, so per-directory warnings would be noise.
DIRECTORY_SCOPED_ECOSYSTEMS = {"github-actions", "npm", "bun", "cargo", "gomod"}


# =============================================================================
# Ecosystem detection
# =============================================================================


def detect_ecosystems(repo: Repository) -> dict[str, set[str]]:
    """Map each ecosystem the repository uses to its manifest directories."""
    found: dict[str, set[str]] = {}

    def add(ecosystem: str, path: str) -> None:
        found.setdefault(ecosystem, set()).add(parent(path))

    lock_dirs = {parent(p) for p in repo.files if name(p) in NPM_LOCKFILES + BUN_LOCKFILES}
    for path in repo.files:
        base = name(path)
        if path.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
            found.setdefault("github-actions", set()).add("")
        elif base in ("action.yml", "action.yaml"):
            add("github-actions", path)
        elif base in BUN_LOCKFILES:
            add("bun", path)
        elif base in NPM_LOCKFILES:
            add("npm", path)
        elif base == "package.json" and not under_any(parent(path), lock_dirs):
            # Workspace members are updated through their root's lockfile.
            if has_js_dependencies(repo, path):
                add("npm", path)
        elif base == "Cargo.toml":
            pass  # added below: only workspace roots and standalone packages
        elif base == "go.mod":
            if re.search(r"^\s*require\b", repo.text(path), re.MULTILINE):
                add("gomod", path)
        elif base in PYTHON_MANIFESTS or re.fullmatch(r"requirements[\w.-]*\.txt", base):
            add("python", path)
        elif base == "Containerfile" or re.fullmatch(r"(.+\.)?[Dd]ockerfile(\..+)?", base):
            add("docker", path)
        elif re.fullmatch(r"(docker-)?compose([.-].+)?\.ya?ml", base):
            add("docker-compose", path)
        elif base.endswith(".tf"):
            add("terraform", path)
        elif base == "Chart.yaml":
            add("helm", path)
        elif base == "devcontainer.json" or base == ".devcontainer.json":
            add("devcontainers", path)
        elif base in OTHER_MANIFESTS:
            add(OTHER_MANIFESTS[base], path)
    for directory in cargo_roots(repo)[0]:
        found.setdefault("cargo", set()).add(directory)
    return found


def has_js_dependencies(repo: Repository, path: str) -> bool:
    try:
        manifest = json.loads(repo.text(path))
    except json.JSONDecodeError:
        return False
    return any(
        isinstance(manifest.get(key), dict) and manifest[key]
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
    )


NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
BUN_LOCKFILES = ("bun.lock", "bun.lockb")
PYTHON_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "uv.lock", "poetry.lock")
OTHER_MANIFESTS = {
    "Gemfile": "bundler",
    "composer.json": "composer",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "pubspec.yaml": "pub",
    "Package.swift": "swift",
    "mix.exs": "mix",
}


# =============================================================================
# JavaScript package managers
# =============================================================================


def check_pnpm(repo: Repository) -> list[Finding]:
    workspace_dirs = {parent(p) for p in repo.files if name(p) == "pnpm-workspace.yaml"}
    roots = set(workspace_dirs)
    roots |= {parent(p) for p in repo.files if name(p) == "pnpm-lock.yaml"}
    roots |= {
        parent(p)
        for p in repo.files
        if name(p) == "package.json" and package_manager(repo, p)[0] == "pnpm"
    }
    findings = []
    checked: set[str] = set()
    for root in sorted(roots):
        settings_dir = nearest(root, workspace_dirs)
        if settings_dir is not None:
            if settings_dir in checked:
                continue
            checked.add(settings_dir)
            findings += check_pnpm_workspace(repo, join(settings_dir, "pnpm-workspace.yaml"))
        else:
            findings += check_pnpm_without_workspace(repo, root)
        findings += check_pnpm_version(repo, settings_dir if settings_dir is not None else root)
    return findings


def check_pnpm_workspace(repo: Repository, path: str) -> list[Finding]:
    try:
        settings = repo.yaml(path) or {}
    except ParseError as error:
        return [Finding(FAIL, path, f"Cannot parse: {error}", "Fix the YAML syntax.")]
    if not isinstance(settings, dict):
        settings = {}
    return pnpm_findings(
        path,
        settings.get("minimumReleaseAge"),
        settings.get("ignoreScripts"),
        "minimumReleaseAge",
        "ignoreScripts",
    )


def check_pnpm_without_workspace(repo: Repository, root: str) -> list[Finding]:
    """pnpm 10 also read these settings from .npmrc; pnpm 11 does not."""
    workspace = join(root, "pnpm-workspace.yaml")
    npmrc = repo.npmrc(join(root, ".npmrc"))
    age, scripts = npmrc.get("minimum-release-age"), npmrc.get("ignore-scripts")
    if age is None and scripts is None:
        return [
            Finding(
                FAIL,
                workspace,
                f"pnpm project at {root or '/'} has no pnpm-workspace.yaml, so "
                "`minimumReleaseAge` and `ignoreScripts` are unset.",
                f"Create {workspace} with `minimumReleaseAge: {MIN_RELEASE_AGE_MINUTES}` and "
                "`ignoreScripts: true` (no `packages` list is needed for a single project).",
            )
        ]
    findings = pnpm_findings(
        join(root, ".npmrc"), to_int(age), scripts == "true", "minimum-release-age", "ignore-scripts"
    )
    findings.append(
        Finding(
            WARN,
            join(root, ".npmrc"),
            "pnpm settings in .npmrc are ignored by pnpm 11.",
            f"Move them to {workspace} as `minimumReleaseAge` and `ignoreScripts`.",
        )
    )
    return findings


def pnpm_findings(path: str, age: object, scripts: object, age_key: str, scripts_key: str) -> list[Finding]:
    findings = []
    if not is_int(age) or age < MIN_RELEASE_AGE_MINUTES:
        findings.append(
            Finding(
                FAIL,
                path,
                f"`{age_key}` is {describe(age)}; the minimum is {MIN_RELEASE_AGE_MINUTES} "
                "(7 days, in minutes).",
                f"Set `{age_key}: {MIN_RELEASE_AGE_MINUTES}`. Exempt urgent security fixes "
                "with `minimumReleaseAgeExclude` entries (`name@version`).",
            )
        )
    if scripts is not True:
        findings.append(
            Finding(
                FAIL,
                path,
                f"`{scripts_key}` is {describe(scripts)}; dependency lifecycle scripts may run.",
                f"Set `{scripts_key}: true` and run `pnpm rebuild <pkg>` for reviewed "
                "packages that need a build step.",
            )
        )
    return findings


def check_pnpm_version(repo: Repository, root: str) -> list[Finding]:
    manager, version = package_manager(repo, join(root, "package.json"))
    if manager != "pnpm" or version is None or version >= PNPM_MIN_RELEASE_AGE_VERSION:
        return []
    return [
        Finding(
            FAIL,
            join(root, "package.json"),
            f"`packageManager` pins pnpm {'.'.join(map(str, version))}, which ignores "
            "`minimumReleaseAge` (added in 10.16.0).",
            "Upgrade `packageManager` to pnpm 10.16.0 or later.",
        )
    ]


def check_npm(repo: Repository) -> list[Finding]:
    findings = []
    for lockfile in sorted(p for p in repo.files if name(p) in ("package-lock.json", "npm-shrinkwrap.json")):
        root = parent(lockfile)
        npmrc_path = join(root, ".npmrc")
        npmrc = repo.npmrc(npmrc_path)
        age = parse_duration(npmrc.get("min-release-age"), default_unit=86400)
        problems = []
        if age is None or age < MIN_AGE_SECONDS:
            problems.append(f"`min-release-age` is {describe(npmrc.get('min-release-age'))}")
        if npmrc.get("ignore-scripts") != "true":
            problems.append(f"`ignore-scripts` is {describe(npmrc.get('ignore-scripts'))}")
        if problems:
            findings.append(
                Finding(
                    WARN,
                    npmrc_path,
                    f"npm project at {root or '/'}: {'; '.join(problems)}.",
                    "Add `min-release-age=7` and `ignore-scripts=true` to this .npmrc "
                    "(min-release-age needs a recent npm 11), or move the project to pnpm.",
                )
            )
    return findings


def check_yarn(repo: Repository) -> list[Finding]:
    rc_dirs = {parent(p) for p in repo.files if name(p) == ".yarnrc.yml"}
    findings = []
    for lockfile in sorted(p for p in repo.files if name(p) == "yarn.lock"):
        root = parent(lockfile)
        rc_dir = nearest(root, rc_dirs)
        if rc_dir is None:
            findings.append(
                Finding(
                    WARN,
                    lockfile,
                    "Yarn Classic has no release-age gate.",
                    "Move the project to pnpm, or to Yarn 4 with `npmMinimalAgeGate: 7d`.",
                )
            )
            continue
        path = join(rc_dir, ".yarnrc.yml")
        try:
            rc = repo.yaml(path) or {}
        except ParseError as error:
            findings.append(Finding(WARN, path, f"Cannot parse: {error}", "Fix the YAML syntax."))
            continue
        gate = rc.get("npmMinimalAgeGate") if isinstance(rc, dict) else None
        age = parse_duration(gate, default_unit=60)
        if age is None or age < MIN_AGE_SECONDS:
            findings.append(
                Finding(WARN, path, f"`npmMinimalAgeGate` is {describe(gate)}.", "Set `npmMinimalAgeGate: 7d`.")
            )
        if isinstance(rc, dict) and rc.get("enableScripts") is True:
            findings.append(
                Finding(WARN, path, "`enableScripts: true` runs dependency lifecycle scripts.", "Remove it.")
            )
    return findings


def check_bun(repo: Repository) -> list[Finding]:
    findings = []
    for lockfile in sorted(p for p in repo.files if name(p) in BUN_LOCKFILES):
        root = parent(lockfile)
        path = join(root, "bunfig.toml")
        value = None
        if repo.has(path):
            try:
                value = tomllib.loads(repo.text(path)).get("install", {}).get("minimumReleaseAge")
            except tomllib.TOMLDecodeError:
                pass
        age = parse_duration(value, default_unit=1)
        if age is None or age < MIN_AGE_SECONDS:
            findings.append(
                Finding(
                    WARN,
                    path,
                    f"Bun project at {root or '/'}: `install.minimumReleaseAge` is {describe(value)}.",
                    f"Add `[install]` `minimumReleaseAge = {MIN_AGE_SECONDS}` (seconds) to bunfig.toml.",
                )
            )
    return findings


def package_manager(repo: Repository, path: str) -> tuple[str | None, tuple[int, ...] | None]:
    if not repo.has(path):
        return None, None
    try:
        value = json.loads(repo.text(path)).get("packageManager")
    except (json.JSONDecodeError, AttributeError):
        return None, None
    match = re.fullmatch(r"([a-z]+)@(\d+)\.(\d+)\.(\d+).*", str(value or ""))
    if not match:
        return (str(value).split("@")[0] or None) if value else None, None
    return match.group(1), tuple(int(match.group(i)) for i in (2, 3, 4))


# =============================================================================
# Rust
# =============================================================================


def check_cargo(repo: Repository) -> list[Finding]:
    roots, manifests, workspaces = cargo_roots(repo)
    findings = []
    for directory in roots:
        lockfile = join(directory, "Cargo.lock")
        if not repo.has(lockfile):
            findings.append(
                Finding(
                    FAIL,
                    lockfile,
                    f"Cargo {'workspace' if directory in workspaces else 'package'} "
                    f"{manifests[directory]} has no committed Cargo.lock.",
                    "Run `cargo generate-lockfile` and commit Cargo.lock (remove it from "
                    ".gitignore if listed).",
                )
            )
    findings += check_cargo_locked(repo)
    return findings


def cargo_roots(repo: Repository) -> tuple[list[str], dict[str, str], dict[str, dict]]:
    """Directories that resolve their own Cargo.lock: workspace roots and
    packages outside any workspace."""
    manifests = {parent(p): p for p in repo.files if name(p) == "Cargo.toml"}
    workspaces = {}
    for directory, path in manifests.items():
        try:
            workspace = tomllib.loads(repo.text(path)).get("workspace")
        except tomllib.TOMLDecodeError:
            continue
        if isinstance(workspace, dict):
            workspaces[directory] = workspace
    roots = [
        d for d in sorted(manifests) if d in workspaces or not workspace_member(d, workspaces)
    ]
    return roots, manifests, workspaces


def workspace_member(directory: str, workspaces: dict[str, dict]) -> bool:
    for root, workspace in workspaces.items():
        if not is_under(directory, root):
            continue
        relative = directory[len(root) :].lstrip("/") if root else directory
        excluded = workspace.get("exclude") or []
        if any(relative == normalize_dir(e) or relative.startswith(normalize_dir(e) + "/") for e in excluded):
            continue
        return True
    return False


def check_cargo_locked(repo: Repository) -> list[Finding]:
    findings = []
    for path in sorted(workflow_files(repo)):
        try:
            document = repo.yaml(path)
        except ParseError:
            continue  # zizmor and actionlint report malformed workflows
        for script in run_scripts(document):
            for command in shell_commands(script):
                match = CARGO_COMMAND.search(command)
                if not match or LOCKED_FLAG.search(command):
                    continue
                subcommand = match.group("sub")
                findings.append(
                    Finding(
                        FAIL if subcommand == "install" else WARN,
                        path,
                        f"`{shorten(command)}` runs without --locked, so Cargo may "
                        + (
                            "resolve the newest matching versions instead of a lockfile."
                            if subcommand == "install"
                            else "rewrite Cargo.lock instead of failing when it is stale."
                        ),
                        f"Add `--locked` to `cargo {subcommand}`.",
                    )
                )
    return findings


CARGO_COMMAND = re.compile(
    r"(?:^|[\s;&|(])cargo\s+(?:\+\S+\s+)?"
    r"(?P<sub>build|test|clippy|install|run|check|doc|bench|nextest|llvm-cov)\b"
)
LOCKED_FLAG = re.compile(r"\s--(?:locked|frozen)\b")


def workflow_files(repo: Repository) -> list[str]:
    return [
        p
        for p in repo.files
        if (p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml")))
        or name(p) in ("action.yml", "action.yaml")
    ]


def run_scripts(document: object) -> list[str]:
    scripts = []
    if isinstance(document, dict):
        for key, value in document.items():
            if key == "run" and isinstance(value, str):
                scripts.append(value)
            else:
                scripts.extend(run_scripts(value))
    elif isinstance(document, list):
        for item in document:
            scripts.extend(run_scripts(item))
    return scripts


def shell_commands(script: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", script)
    commands = []
    for line in joined.splitlines():
        line = line.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            commands.extend(c.strip() for c in re.split(r"&&|\|\||;|\|", line) if c.strip())
    return commands


# =============================================================================
# Python (uv)
# =============================================================================


def check_uv(repo: Repository) -> list[Finding]:
    findings = []
    for lockfile in sorted(p for p in repo.files if name(p) == "uv.lock"):
        root = parent(lockfile)
        value, source = uv_exclude_newer(repo, root)
        if value is None:
            findings.append(
                Finding(
                    WARN,
                    join(root, "pyproject.toml"),
                    f"uv project at {root or '/'} has no `exclude-newer`.",
                    'Add `exclude-newer = "7 days"` under `[tool.uv]` (needs a uv release '
                    "that accepts durations; older uv rejects the setting).",
                )
            )
            continue
        age = parse_duration(value, default_unit=None)
        if age is not None and age < MIN_AGE_SECONDS:
            findings.append(
                Finding(
                    WARN,
                    source,
                    f"`exclude-newer` is {describe(value)}; the minimum is 7 days.",
                    'Set `exclude-newer = "7 days"`.',
                )
            )
    return findings


def uv_exclude_newer(repo: Repository, root: str) -> tuple[object, str]:
    for candidate, table in ((join(root, "uv.toml"), ()), (join(root, "pyproject.toml"), ("tool", "uv"))):
        if not repo.has(candidate):
            continue
        try:
            data = tomllib.loads(repo.text(candidate))
        except tomllib.TOMLDecodeError:
            continue
        for key in table:
            data = data.get(key, {}) if isinstance(data, dict) else {}
        if isinstance(data, dict) and "exclude-newer" in data:
            return data["exclude-newer"], candidate
    return None, join(root, "pyproject.toml")


# =============================================================================
# Reporting
# =============================================================================


def report(findings: list[Finding]) -> None:
    failures = [f for f in findings if f.level == FAIL]
    warnings = [f for f in findings if f.level == WARN]
    annotate = in_github_actions()
    for finding in findings:
        label = "error" if finding.level == FAIL else "warning"
        if annotate:
            print(f"::{label} file={finding.path},title=Supply-chain policy::{finding.message} Fix: {finding.fix}")
        else:
            print(f"{label}: {finding.path}: {finding.message}\n  fix: {finding.fix}")
    summary = f"Supply-chain policy: {len(failures)} failure(s), {len(warnings)} warning(s)."
    print(summary)
    if not failures:
        print("Passed." if not warnings else "Passed with warnings.")
    print(f"Policy and fixes: {POLICY_DOC}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if annotate and summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"## {summary}\n\n")
            if findings:
                handle.write("| Level | File | Finding | Fix |\n| --- | --- | --- | --- |\n")
                for f in findings:
                    cells = [f.level, f"`{f.path}`", f.message, f.fix]
                    handle.write("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |\n")
            handle.write(f"\n[Policy and fixes]({POLICY_DOC})\n")


def in_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


# =============================================================================
# Repository access
# =============================================================================


class ToolError(Exception):
    pass


class ParseError(Exception):
    pass


class Repository:
    """Tracked files of a checkout, minus vendored code and test fixtures."""

    def __init__(self, root: Path):
        self.root = root
        self.files = sorted(p for p in list_files(root) if not ignored(p))
        self._file_set = set(self.files)

    def has(self, path: str) -> bool:
        return path in self._file_set

    def text(self, path: str) -> str:
        try:
            return (self.root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def yaml(self, path: str) -> object:
        result = subprocess.run(
            [yq_binary(), "--input-format=yaml", "--output-format=json", "--indent=0", "eval", "."],
            input=self.text(path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            lines = result.stderr.strip().splitlines()
            raise ParseError(lines[-1] if lines else "invalid YAML")
        output = result.stdout.strip()
        if not output:
            return None
        value, _ = json.JSONDecoder().raw_decode(output)  # first document only
        return value

    def npmrc(self, path: str) -> dict[str, str]:
        if not self.has(path):
            return {}
        parser = configparser.ConfigParser(
            delimiters=("=",), comment_prefixes=("#", ";"), interpolation=None, strict=False
        )
        try:
            parser.read_string("[npmrc]\n" + self.text(path))
        except configparser.Error:
            return {}
        return {k.strip(): v.strip().strip("\"'") for k, v in parser["npmrc"].items()}


def yq_binary() -> str:
    global _YQ
    if _YQ is None:
        yq = shutil.which("yq")
        version = ""
        if yq is not None:
            version = subprocess.run([yq, "--version"], capture_output=True, text=True, check=False).stdout
        if "mikefarah" not in version or not re.search(r"\bv?4\.", version):
            raise ToolError("mikefarah yq v4 (https://github.com/mikefarah/yq) must be on PATH to read YAML.")
        _YQ = yq
    return _YQ


_YQ: str | None = None


def list_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False
    )
    if result.returncode == 0 and is_git_root(root):
        return [p for p in result.stdout.decode().split("\0") if p]
    files = []
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d != ".git"]
        for entry in names:
            files.append(Path(directory, entry).relative_to(root).as_posix())
    return files


def is_git_root(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == root.resolve()


def ignored(path: str) -> bool:
    return any(part in IGNORED_DIRECTORIES for part in PurePosixPath(path).parts[:-1])


IGNORED_DIRECTORIES = {
    "node_modules",
    "vendor",
    "third_party",
    "third-party",
    "testdata",
    "fixtures",
    "test-fixtures",
    "__fixtures__",
}


# =============================================================================
# Helpers
# =============================================================================


def parse_duration(value: object, default_unit: int | None) -> int | None:
    """Seconds in a number (in `default_unit` seconds) or a duration string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if default_unit is None else int(value * default_unit)
    text = str(value).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return None if default_unit is None else int(float(text) * default_unit)
    iso = re.fullmatch(r"p(?:(\d+)w)?(?:(\d+)d)?(?:t(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?)?", text)
    if iso and any(iso.groups()):
        weeks, days, hours, minutes, seconds = (int(g or 0) for g in iso.groups())
        return (((weeks * 7 + days) * 24 + hours) * 60 + minutes) * 60 + seconds
    total, matched = 0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", text):
        factor = DURATION_UNITS.get(unit)
        if factor is None:
            return None
        total += int(float(amount) * factor)
        matched = True
    return total if matched else None


DURATION_UNITS = {
    **dict.fromkeys(("s", "sec", "secs", "second", "seconds"), 1),
    **dict.fromkeys(("m", "min", "mins", "minute", "minutes"), 60),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), 3600),
    **dict.fromkeys(("d", "day", "days"), 86400),
    **dict.fromkeys(("w", "week", "weeks"), 604800),
}


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def to_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def describe(value: object) -> str:
    return "unset" if value is None else f"`{json.dumps(value) if not isinstance(value, str) else value}`"


def shorten(command: str, limit: int = 80) -> str:
    return command if len(command) <= limit else command[: limit - 3] + "..."


def format_dirs(dirs: list[str], limit: int = 5) -> str:
    shown = ["/" + d for d in dirs[:limit]]
    return ", ".join(shown) + (f" and {len(dirs) - limit} more" if len(dirs) > limit else "")


def parent(path: str) -> str:
    value = PurePosixPath(path).parent.as_posix()
    return "" if value == "." else value


def name(path: str) -> str:
    return PurePosixPath(path).name


def join(directory: str, filename: str) -> str:
    return f"{directory}/{filename}" if directory else filename


def is_under(directory: str, root: str) -> bool:
    return root == "" or directory == root or directory.startswith(root + "/")


def under_any(directory: str, roots: set[str]) -> bool:
    return any(is_under(directory, root) for root in roots)


def nearest(directory: str, candidates: set[str]) -> str | None:
    matches = [c for c in candidates if is_under(directory, c)]
    return max(matches, key=len) if matches else None


if __name__ == "__main__":
    sys.exit(main())
