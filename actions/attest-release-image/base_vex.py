#!/usr/bin/env python3
"""Carries Docker Hardened Images' signed VEX statements over to an image built from them.

Docker publishes an OpenVEX attestation for each Docker Hardened Images (DHI)
image (dhi.io/...): which CVEs its packages are not affected by, or have a fix
for. An image built from one, or with packages copied out of one, holds the
same packages, so the same statements apply to it. Scanners only read them for
the DHI image itself, though, so they report those CVEs against ours.

  inherit --image REF --subject-name NAME --dockerfile PATH [--dockerfile PATH ...]
          --output PATH [--platform OS/ARCH]
      Lists the `FROM dhi.io/...@sha256:...` bases of the Dockerfiles, reads
      each base's VEX attestation for each platform of the image, and writes
      the statements that carry over, about `pkg:oci/<last segment of NAME>`,
      as a JSON list (empty when none do). predicates.py `vex --inherited`
      attests them with the project's own statements.

A statement carries over only when all of these hold:
  - Docker signed the attestation, with the key base-images.toml trusts for
    dhi.io, the transparency log (Rekor) records that signature, and it is
    about the base's manifest for that platform. An attestation the key signed
    but Rekor does not record is skipped with a warning; a bad signature fails.
  - It is `fixed`, or `not_affected` because the package lacks the code
    (vulnerable_code_not_present, component_not_present): facts about the
    package build that hold wherever it runs. For a package Docker built
    itself, `not_affected` because of inline_mitigations_already_exist (its
    patch in that build) too. A justification that depends on how the DHI
    image runs the code is left out.
  - The image holds the same build of the package as the base, on every one of
    its platforms:
      - Alpine (apk): the same name, version and checksum (the `C:` line of
        the apk database).
      - Debian (dpkg, by binary or source package name): the same version, of
        a package Docker built (maintainer Docker Hardened Images, a `+dhi`
        version, which no other build carries). Debian's own builds record no
        checksum to compare, so they never match.

Needs on PATH: oras, cosign and docker, logged in to dhi.io and to the image's
registry.
"""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

OPENVEX = "https://openvex.dev/ns/v0.2.0"
IN_TOTO = "application/vnd.in-toto+json"
PREDICATE_TYPE_ANNOTATION = "in-toto.io/predicate-type"
# Statements that hold for the package build wherever it is installed.
CARRIED = {("not_affected", "vulnerable_code_not_present"), ("not_affected", "component_not_present"), ("fixed", None)}
# Only for a package Docker built: the mitigation is its patch in that build.
CARRIED_FOR_DOCKER_BUILDS = {("not_affected", "inline_mitigations_already_exist")}
PURL = re.compile(r"^pkg:(?P<type>apk|deb)/(?P<namespace>[^/@?#]+)/(?P<name>[^/@?#]+)@(?P<version>[^?#]+)")
DOCKER_MAINTAINER = "Docker Hardened Images"
VULNERABILITY = re.compile(r"(CVE-\d{4}-\d{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3})")
PINNED = re.compile(r"^(?P<repository>[^@]+?)(?::[\w][\w.-]{0,127})?@(?P<digest>sha256:[0-9a-f]{64})$")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
HERE = Path(__file__).resolve().parent
POLICY = HERE.parent.parent / "supply-chain-policy"
# Bases checked at once: each is a few oras and cosign calls, and maybe a pull.
PARALLEL = 8


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    inherit_command = commands.add_parser("inherit")
    inherit_command.add_argument("--image", required=True, help="The image, pinned by digest.")
    inherit_command.add_argument("--subject-name", required=True)
    inherit_command.add_argument("--dockerfile", action="append", required=True)
    inherit_command.add_argument("--output", required=True)
    inherit_command.add_argument("--platform", default="linux/amd64", help="The image's platform when it is a single manifest.")
    args = parser.parse_args(argv)

    bases = dhi_bases([Path(path) for path in args.dockerfile])
    print(f"Docker Hardened Images bases: {', '.join(bases) or 'none'}")
    statements = inherit(Registry(key=dhi_key()), image=args.image, subject_name=args.subject_name,
                         bases=bases, single_platform=args.platform)
    Path(args.output).write_text(json.dumps(statements, indent=2) + "\n", encoding="utf-8")
    print(f"{len(statements)} statement(s) carried over from the bases' VEX")
    return 0


def dhi_bases(dockerfiles):
    """The dhi.io images, pinned by digest, that the Dockerfiles pull."""
    policy = load(POLICY / "check_supply_chain_policy.py", "check_supply_chain_policy")
    bases = []
    for path in dockerfiles:
        for image in policy.dockerfile_images(str(path), path.read_text(encoding="utf-8")):
            # The registry host itself, not a prefix of the reference.
            if image.resolved and image.resolved.split("/", 1)[0] == "dhi.io":
                if not PINNED.match(image.resolved):
                    raise ValueError(f"{path}:{image.line}: {image.resolved} is not pinned by digest")
                bases.append(image.resolved)
    return list(dict.fromkeys(bases))


def inherit(registry, *, image, subject_name, bases, single_platform="linux/amd64"):
    """The base statements that hold for `image` on all its platforms, as project statements."""
    if not PINNED.match(image):
        raise ValueError(f"image must be pinned by digest: {image}")
    platforms = image_platforms(registry, image, single_platform)
    product = f"pkg:oci/{subject_name.rsplit('/', 1)[-1]}"
    packages = {platform: parse_packages(registry.package_db(ours, platform)) for platform, ours in platforms}
    # Each base on each platform is checked on its own: registry and Rekor
    # round trips, and a pull for a base with a statement the image may share,
    # so they run side by side. Results are combined in order, as before.
    pairs = [(platform, base) for platform, _ in platforms for base in bases]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(pairs)))) as pool:
        results = list(pool.map(lambda pair: base_statements(registry, pair[1], pair[0], packages[pair[0]]), pairs))
    by_platform = {}
    for (platform, _), statements in zip(pairs, results):
        by_platform.setdefault(platform, []).append(statements)
    carried = None
    for platform, _ in platforms:
        found = {}
        for statements in by_platform.get(platform, []):
            for key, statement in statements.items():
                found.setdefault(key, statement)
        # A statement about the image must hold, saying the same, on each of its platforms.
        carried = found if carried is None else {key: value for key, value in carried.items()
                                                 if key in found and same_claim(found[key], value)}
    return grouped(carried or {}, product)


def same_claim(one, other):
    return (one["status"], one["justification"]) == (other["status"], other["justification"])


def image_platforms(registry, image, single_platform):
    """[(platform, reference of its manifest)] for the image."""
    repository = PINNED.match(image)["repository"]
    manifest = registry.manifest(image)
    if manifest.get("mediaType") not in INDEX_TYPES and "manifests" not in manifest:
        return [(single_platform, image)]
    platforms = []
    for entry in manifest["manifests"]:
        platform = entry.get("platform") or {}
        if platform.get("os") in (None, "unknown"):
            continue  # buildx's attestation manifests
        name = "/".join(part for part in (platform["os"], platform["architecture"], platform.get("variant")) if part)
        platforms.append((name, f"{repository}@{entry['digest']}"))
    if not platforms:
        raise ValueError(f"{image} lists no platform")
    return platforms


def base_statements(registry, base, platform, packages):
    """{(vulnerability, package purl): what the newest statement about them says}, from one base."""
    repository = PINNED.match(base)["repository"]
    digest = select_platform(registry.manifest(base), platform)
    if digest is None:
        print(f"  {base} has no {platform} image: nothing carried over from it")
        return {}
    # The statements that can carry over, from the image's packages alone: it
    # must hold the package at the version the statement names, with an
    # identity to compare. Only then is the base pulled, to compare builds:
    # most bases (build stages, other images' bases) share no such package.
    candidates = []
    for document, source in vex_documents(registry, repository, digest):
        for statement in document.get("statements") or []:
            name = (statement.get("vulnerability") or {}).get("name", "")
            status, justification = statement.get("status"), statement.get("justification")
            claim = (status, justification if status == "not_affected" else None)
            if not VULNERABILITY.fullmatch(name) or claim not in CARRIED | CARRIED_FOR_DOCKER_BUILDS:
                continue
            for purl in statement_packages(statement):
                match = PURL.match(purl)
                if not match:
                    continue
                package, version = (match["type"], match["name"]), urllib.parse.unquote(match["version"])
                ours = packages.get(package)
                if not ours or ours["version"] != version or not ours["identity"]:
                    continue
                if claim in CARRIED_FOR_DOCKER_BUILDS and not ours["docker"]:
                    continue
                candidates.append((document, source, statement, name, status, justification, match, package))
    if not candidates:
        return {}
    theirs = parse_packages(registry.package_db(f"{repository}@{digest}", platform))
    result = {}
    for document, source, statement, name, status, justification, match, package in candidates:
        # The base's own build of the package, and the image holds that same build:
        # its identity proves it (a checksum, or a version only Docker's build has),
        # so a package without one never matches.
        if theirs.get(package) != packages[package]:
            continue
        key = (name, f"pkg:{match['type']}/{match['namespace']}/{match['name']}@{match['version']}")
        result.setdefault(key, {
            "status": status,
            "justification": justification if status == "not_affected" else None,
            "timestamp": statement.get("timestamp") or document.get("last_updated") or document["timestamp"],
            "impact": (statement.get("impact_statement") or "").strip(),
            "source": source,
            "base": base.split("@", 1)[0],
        })
    return result


def select_platform(index, platform):
    """The digest of the index's manifest for `platform`, or None."""
    os_name, architecture, *variant = platform.split("/")
    for entry in index.get("manifests") or []:
        wanted = entry.get("platform") or {}
        if wanted.get("os") == os_name and wanted.get("architecture") == architecture \
                and (not variant or not wanted.get("variant") or wanted["variant"] == variant[0]):
            return entry["digest"]
    return None


def vex_documents(registry, repository, digest):
    """[(OpenVEX predicate, its @id)] attested for `repository@digest`, each signature checked."""
    documents = []
    for descriptor in registry.referrers(f"{repository}@{digest}"):
        if descriptor.get("artifactType") != IN_TOTO or \
                (descriptor.get("annotations") or {}).get(PREDICATE_TYPE_ANNOTATION) != OPENVEX:
            continue
        reference = f"{repository}@{descriptor['digest']}"
        # Docker signs the attestation's manifest; the manifest pins its layer by digest.
        if not registry.verify(reference):
            print(f"::warning::{reference}: Docker's signature is valid but not in the transparency log; "
                  "its statements are not carried over")
            continue
        manifest_bytes = registry.manifest_bytes(reference)
        check_digest(manifest_bytes, descriptor["digest"], reference)
        layers = [layer for layer in json.loads(manifest_bytes).get("layers") or [] if layer.get("mediaType") == IN_TOTO]
        if len(layers) != 1:
            raise ValueError(f"{reference}: expected one in-toto layer, found {len(layers)}")
        statement_bytes = registry.blob(f"{repository}@{layers[0]['digest']}")
        check_digest(statement_bytes, layers[0]["digest"], reference)
        statement = json.loads(statement_bytes)
        if statement.get("predicateType") != OPENVEX:
            raise ValueError(f"{reference}: not an OpenVEX attestation")
        if not any(subject.get("digest", {}).get("sha256") == digest.removeprefix("sha256:")
                   for subject in statement.get("subject") or []):
            raise ValueError(f"{reference}: the attestation is not about {repository}@{digest}")
        predicate = statement.get("predicate") or {}
        documents.append((predicate, predicate.get("@id") or reference))
    # The newest document first: its statement wins a package both name.
    documents.sort(key=lambda item: item[0].get("last_updated") or item[0].get("timestamp") or "", reverse=True)
    return documents


def statement_packages(statement):
    """The package URLs a statement is about: its products' subcomponents, or package products."""
    purls = [component.get("@id", "") for product in statement.get("products") or []
             for component in product.get("subcomponents") or []]
    if not purls:
        purls = [product.get("@id", "") for product in statement.get("products") or []]
    return [purl for purl in dict.fromkeys(purls) if purl.startswith("pkg:")]


def grouped(carried, product):
    """Project statements: one per vulnerability, status and source, listing its packages."""
    groups = {}
    for (name, purl), details in sorted(carried.items()):
        key = (name, details["status"], details["justification"], details["source"])
        group = groups.setdefault(key, {"details": details, "purls": []})
        group["purls"].append(purl)
    statements = []
    for (name, status, justification, source), group in groups.items():
        details = group["details"]
        packages = ", ".join(purl.split("/", 2)[2] for purl in group["purls"])
        statement = {
            "vulnerability": {"name": name},
            "products": [{"@id": product, "subcomponents": [{"@id": purl} for purl in group["purls"]]}],
            "status": status,
            "timestamp": details["timestamp"],
            "status_notes": f"From the VEX Docker signed for {details['base']} ({source}). This image holds the same package build.",
        }
        if status == "not_affected":
            statement["justification"] = justification
            statement["impact_statement"] = details["impact"] or \
                f"Docker Hardened Images: {packages} {justification.replace('_', ' ')}."
        statements.append(statement)
    return statements


def parse_packages(databases):
    """{(purl type, package): {version, identity, docker}} from an image's package databases:
    {"apk": /lib/apk/db/installed, "dpkg": [/var/lib/dpkg/status, /var/lib/dpkg/status.d/*]}.
    `identity` proves which build it is, or is None when nothing does."""
    packages = {}
    for block in (databases.get("apk") or "").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if len(line) > 2 and line[1] == ":" and line[0] in "PVCm":
                fields.setdefault(line[0], line[2:])
        if "P" in fields and "V" in fields:
            packages[("apk", fields["P"])] = {"version": fields["V"], "identity": fields.get("C"),
                                              "docker": fields.get("m", "").startswith(DOCKER_MAINTAINER)}
    for text in databases.get("dpkg") or []:
        for block in text.split("\n\n"):
            fields = dict(re.findall(r"^([A-Za-z-]+): ?(.*)$", block, re.M))
            if "Package" not in fields or "Version" not in fields or fields.get("Status", "install ok installed").split()[-1] != "installed":
                continue
            version = fields["Version"]
            docker = fields.get("Maintainer", "").startswith(DOCKER_MAINTAINER) and "+dhi" in version
            build = {"version": version, "identity": version if docker else None, "docker": docker}
            # Advisories name the source package (glibc), images install its binaries (libc6).
            source = fields.get("Source", fields["Package"]).split(" ", 1)[0]
            for name in dict.fromkeys((fields["Package"], source)):
                packages.setdefault(("deb", name), build)
    return packages


def check_digest(content, digest, where):
    if f"sha256:{hashlib.sha256(content).hexdigest()}" != digest:
        raise ValueError(f"{where}: content does not match {digest}")


def dhi_key():
    """The public key base-images.toml trusts for dhi.io."""
    verify = load(POLICY / "verify_base_images.py", "verify_base_images")
    rule = verify.match("dhi.io/node", verify.load_trust(POLICY / "base-images.toml"))
    if not rule or not rule.signed or rule.options.get("method") != "cosign" or not rule.options.get("key"):
        raise ValueError("base-images.toml trusts no cosign key for dhi.io")
    return rule.options["key"]


def load(path, name):
    """A module by path: python3 -I keeps the script's directory off sys.path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


class Registry:
    """oras, cosign and docker: the only process boundary, replaced in tests."""

    def __init__(self, key):
        self.key = key

    def manifest(self, reference):
        content = self.manifest_bytes(reference)
        check_digest(content, PINNED.match(reference)["digest"], reference)
        return json.loads(content)

    def manifest_bytes(self, reference):
        return run(["oras", "manifest", "fetch", reference]).stdout

    def referrers(self, reference):
        listing = json.loads(run(["oras", "discover", "--format", "json", reference]).stdout)
        return [item for item in walk(listing) if "digest" in item and "artifactType" in item]

    def blob(self, reference):
        return run(["oras", "blob", "fetch", "--output", "-", reference]).stdout

    def verify(self, reference):
        """True when Docker's key signed it and Rekor logged that; False when only the key signed it."""
        command = ["cosign", "verify", "--key", self.key, "--experimental-oci11", reference]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode == 0:
            return True
        if b"not found in transparency log" in result.stderr:
            run([*command, "--insecure-ignore-tlog=true"])  # still fails on a bad signature
            return False
        run(command)

    def package_db(self, reference, platform):
        """The image's apk database and dpkg status files (see parse_packages); missing ones are empty."""
        run(["docker", "pull", "--quiet", "--platform", platform, reference])
        container = run(["docker", "create", "--platform", platform, reference, "/base-vex-not-run"]).stdout.decode().strip()
        try:
            with tempfile.TemporaryDirectory() as directory:
                found = {}
                for name, path in (("apk", "/lib/apk/db/installed"), ("status", "/var/lib/dpkg/status"),
                                   ("status.d", "/var/lib/dpkg/status.d")):
                    result = subprocess.run(["docker", "cp", f"{container}:{path}", os.path.join(directory, name)],
                                            capture_output=True, check=False)
                    found[name] = result.returncode == 0
                read = lambda path: path.read_text(encoding="utf-8", errors="replace")
                root = Path(directory)
                dpkg = ([read(root / "status")] if found["status"] else []) + \
                    ([read(path) for path in sorted((root / "status.d").iterdir()) if path.is_file()] if found["status.d"] else [])
                return {"apk": read(root / "apk") if found["apk"] else "", "dpkg": dpkg}
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
            # Its disk space, for the malware scan running meanwhile. Not forced:
            # an image a container still uses stays.
            subprocess.run(["docker", "image", "rm", reference], capture_output=True, check=False)


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def run(command):
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()[-1:] or [""]
        raise ValueError(f"{' '.join(command[:3])} failed: {detail[0]}")
    return result


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.exit(f"::error::base_vex.py: {error}")
