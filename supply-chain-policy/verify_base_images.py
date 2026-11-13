#!/usr/bin/env python3
"""Verify the signatures of the images Dockerfiles build on.

Every image a Dockerfile pulls (`FROM`, `COPY --from`) must match an entry of
base-images.toml beside this file. A [[signed]] image must verify with its
publisher's signature; an [[unsigned]] one is allowed as listed; any other
image fails. A `# supply-chain-policy: allow-unverified-image <reason>` marker
on the line, or on the `ARG` that names the image, allows one reference.

With --base-ref, only references that are new since that commit are checked,
so a pull request verifies what it introduces. Without it, every reference is.

Needs Python 3.11+, git, `gh` (for method "github") and `cosign` (for method
"cosign") on PATH; `gh` reads GH_TOKEN.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
TRUST_FILE = HERE / "base-images.toml"
ALLOW_MARKER = "supply-chain-policy: allow-unverified-image"
RETRY_DELAYS = (3, 10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    parser.add_argument("--base-ref", help="check only references new since this commit")
    parser.add_argument("--trust", default=str(TRUST_FILE), help="trust list (base-images.toml)")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    trust = load_trust(Path(args.trust))
    results = check(root, trust, Verifier(), base_ref=args.base_ref)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        report(results)
    return 1 if any(r.level == policy.FAIL for r in results) else 0


# =============================================================================
# Checking
# =============================================================================


@dataclass(frozen=True)
class Result:
    level: str  # policy.FAIL, policy.WARN, or "ok"
    path: str
    line: int
    image: str
    status: str  # verified, unsigned, allowed, unresolved, unknown, failed
    detail: str


@dataclass(frozen=True)
class Rule:
    repository: str
    signed: bool
    options: dict

    def describe(self) -> str:
        if not self.signed:
            return f"listed unsigned: {self.options['reason']}"
        method = self.options["method"]
        who = self.options.get("owner") or self.options.get("repo") or self.options.get("identity") \
            or self.options.get("identity_regexp")
        return f"{method} signature by {who}"


def check(root: Path, trust: list[Rule], verifier: "Verifier", base_ref: str | None = None) -> list[Result]:
    images = [image for path, text in head_dockerfiles(root) for image in policy.dockerfile_images(path, text)]
    if base_ref:
        before = {image.resolved for path, text in base_dockerfiles(root, base_ref)
                  for image in policy.dockerfile_images(path, text)}
        images = [image for image in images if image.resolved is None or image.resolved not in before]
    results = []
    for image in images:
        results.append(judge(image, trust, verifier))
    return results


def judge(image, trust: list[Rule], verifier: "Verifier") -> Result:
    where = (image.path, image.line)
    if image.resolved is None:
        return Result(policy.WARN, *where, image.written, "unresolved",
                      "An ARG in it has no default, so the image is only known at build time and "
                      "cannot be checked here.")
    if allowed(image):
        return Result("ok", *where, image.resolved, "allowed", f"`{ALLOW_MARKER}` marker")
    rule = match(repository_of(image.resolved), trust)
    if rule is None:
        return Result(policy.FAIL, *where, image.resolved, "unknown",
                      f"`{repository_of(image.resolved)}` is not in the base-image trust list.")
    if not rule.signed:
        return Result("ok", *where, image.resolved, "unsigned", rule.describe())
    error = verifier.verify(image.resolved, rule)
    if error is None:
        return Result("ok", *where, image.resolved, "verified", rule.describe())
    return Result(policy.FAIL, *where, image.resolved, "failed",
                  f"Expected a {rule.describe()}; verification failed: {error}")


def allowed(image) -> bool:
    """The marker sits on the image's line, or on the `ARG` line naming it, or
    in the comment block directly above one of those. Unlike the other markers
    it does not reach further up, so one marker allows exactly one image."""
    for index in image.indexes:
        if ALLOW_MARKER in image.lines[index]:
            return True
        above = index - 1
        while above >= 0 and image.lines[above].strip().startswith("#"):
            if ALLOW_MARKER in image.lines[above]:
                return True
            above -= 1
    return False


def match(repository: str, trust: list[Rule]) -> Rule | None:
    candidates = [rule for rule in trust if fnmatch.fnmatchcase(repository, rule.repository)]
    # Most specific first: an exact name, then the longest pattern.
    candidates.sort(key=lambda rule: ("*" in rule.repository, -len(rule.repository)))
    return candidates[0] if candidates else None


def repository_of(reference: str) -> str:
    """`node:24@sha256:...` -> `docker.io/library/node`."""
    name = reference.split("@", 1)[0]
    last = name.rsplit("/", 1)
    if ":" in last[-1]:
        name = name.rsplit(":", 1)[0]
    parts = name.split("/")
    if len(parts) == 1 or not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = ["docker.io", *parts]
    if parts[0] in ("index.docker.io", "registry-1.docker.io"):
        parts[0] = "docker.io"
    if parts[0] == "docker.io" and len(parts) == 2:
        parts.insert(1, "library")
    return "/".join(parts).lower()


def load_trust(path: Path) -> list[Rule]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = []
    for signed, key in ((True, "signed"), (False, "unsigned")):
        for entry in data.get(key, []):
            options = {k: v for k, v in entry.items() if k != "repository"}
            if signed and options.get("method") not in ("github", "cosign"):
                raise ValueError(f"{path}: {entry['repository']}: method must be github or cosign")
            if signed and options["method"] == "github" and not (options.get("owner") or options.get("repo")):
                raise ValueError(f"{path}: {entry['repository']}: a github entry needs owner or repo")
            if signed and options["method"] == "cosign" and not (
                    options.get("issuer") and (options.get("identity") or options.get("identity_regexp"))):
                raise ValueError(f"{path}: {entry['repository']}: a cosign entry needs issuer and identity")
            if not signed and not options.get("reason"):
                raise ValueError(f"{path}: {entry['repository']}: an unsigned entry needs a reason")
            rules.append(Rule(entry["repository"], signed, options))
    return rules


# =============================================================================
# Verifying
# =============================================================================


class Verifier:
    """Runs gh or cosign once per reference, retrying failures."""

    def __init__(self, run: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.run = run or (lambda command: subprocess.run(command, capture_output=True, text=True))
        self.sleep = sleep
        self.cache: dict[tuple[str, str], str | None] = {}

    def verify(self, reference: str, rule: Rule) -> str | None:
        """None when it verifies, else the reason it does not."""
        key = (reference, rule.repository)
        if key not in self.cache:
            self.cache[key] = self._verify(reference, rule)
        return self.cache[key]

    def _verify(self, reference: str, rule: Rule) -> str | None:
        command = verify_command(reference, rule.options)
        error = None
        for delay in (*RETRY_DELAYS, None):
            try:
                result = self.run(command)
            except FileNotFoundError:
                return f"`{command[0]}` is not installed"
            if result.returncode == 0:
                return None
            lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
            error = lines[-1] if lines else f"`{command[0]}` exited with {result.returncode}"
            if delay is not None:
                self.sleep(delay)
        return error


def verify_command(reference: str, options: dict) -> list[str]:
    if options["method"] == "github":
        command = ["gh", "attestation", "verify", f"oci://{reference}"]
        command += ["--repo", options["repo"]] if options.get("repo") else ["--owner", options["owner"]]
        if options.get("signer_workflow"):
            command += ["--signer-workflow", options["signer_workflow"]]
        if options.get("bundle_from_oci"):
            command.append("--bundle-from-oci")
        return command
    command = ["cosign", "verify", reference, "--certificate-oidc-issuer", options["issuer"]]
    if options.get("identity"):
        command += ["--certificate-identity", options["identity"]]
    else:
        command += ["--certificate-identity-regexp", options["identity_regexp"]]
    return command


# =============================================================================
# Dockerfiles at HEAD and at the base commit
# =============================================================================


def head_dockerfiles(root: Path) -> list[tuple[str, str]]:
    repo = policy.Repository(root)
    return [(path, repo.text(path)) for path in repo.files if policy.is_dockerfile(path)]


def base_dockerfiles(root: Path, base_ref: str) -> list[tuple[str, str]]:
    listed = git(root, "ls-tree", "-r", "--name-only", base_ref).splitlines()
    paths = [p for p in listed if policy.is_dockerfile(p) and not policy.ignored(p)]
    return [(path, git(root, "show", f"{base_ref}:{path}")) for path in paths]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


# =============================================================================
# Reporting
# =============================================================================


def report(results: list[Result]) -> None:
    findings = [policy.Finding(r.level, r.path, f"Line {r.line}: `{r.image}`: {r.detail}", FIXES[r.status])
                for r in results if r.level != "ok"]
    for r in results:
        if r.level == "ok":
            print(f"ok: {r.path}:{r.line}: {r.image}: {r.detail}")
    if not results:
        print("No new base images to verify.")
    policy.report(findings)


FIXES = {
    "unknown": "Add the repository to supply-chain-policy/base-images.toml in archestra-ai/.github: "
               "[[signed]] with how to verify it, or [[unsigned]] with a reason. For a one-off, mark the "
               f"line with `# {ALLOW_MARKER} <reason>`.",
    "failed": "Check the image comes from its real publisher. If the publisher stopped signing or changed "
              "its signing identity, update base-images.toml; to unblock one reference meanwhile, mark it "
              f"with `# {ALLOW_MARKER} <reason>`.",
    "unresolved": "Give the ARG a default (`ARG BASE_IMAGE=image:tag@sha256:...`) so the image can be checked.",
}


def load_policy():
    """The checker beside this file: it parses Dockerfiles and reports."""
    if "check_supply_chain_policy" in sys.modules:
        return sys.modules["check_supply_chain_policy"]
    spec = importlib.util.spec_from_file_location("check_supply_chain_policy",
                                                  HERE / "check_supply_chain_policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


policy = load_policy()

if __name__ == "__main__":
    sys.exit(main())
