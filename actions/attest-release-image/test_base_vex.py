"""Tests for base_vex.py: which of Docker Hardened Images' VEX statements carry over to an image built from them."""

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("base_vex", Path(__file__).with_name("base_vex.py"))
base_vex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base_vex)
VEX_SPEC = importlib.util.spec_from_file_location("vex", Path(__file__).with_name("vex.py"))
vex = importlib.util.module_from_spec(VEX_SPEC)
VEX_SPEC.loader.exec_module(vex)

PLATFORMS = ("linux/amd64", "linux/arm64")
GIT = "pkg:apk/alpine/git@2.52.1-r0"
TEMPLATE = "pkg:apk/alpine/git-init-template@2.52.1-r0"


def digest(content):
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def apk_db(**packages):
    return "\n\n".join(f"C:{checksum}\nP:{name}\nV:{version}\nA:x86_64" for name, (version, checksum) in packages.items()) + "\n\n"


DHI_GIT = {"git": ("2.52.1-r0", "Q1dhi-git="), "git-init-template": ("2.52.1-r0", "Q1dhi-template=")}


def statement(name="CVE-2025-66413", status="not_affected", justification="vulnerable_code_not_present", purls=(GIT, TEMPLATE)):
    value = {"vulnerability": {"name": name}, "status": status, "timestamp": "2026-05-12T16:01:54Z",
             "products": [{"@id": f"{GIT}?arch=x86_64", "subcomponents": [{"@id": purl} for purl in purls]},
                          {"@id": "https://dhi.io/v2/node/manifests/sha256:" + "0" * 64}]}
    if justification:
        value["justification"] = justification
    return value


def untagged(reference):
    match = base_vex.PINNED.match(reference)
    return f"{match['repository']}@{match['digest']}"


class FakeRegistry:
    """dhi.io and the staging registry in memory: manifests, referrers, blobs, signatures and apk databases."""

    def __init__(self):
        self.content = {}  # reference -> bytes
        self.referrer_lists = {}  # reference -> [descriptor]
        self.signed = set()
        self.unlogged = set()  # signed with Docker's key, but not in Rekor
        self.databases = {}  # (reference, platform) -> apk database text
        self.pulled = []  # the references package_db read, in order

    def put(self, repository, value):
        content = value if isinstance(value, bytes) else json.dumps(value).encode()
        reference = f"{repository}@{digest(content)}"
        self.content[reference] = content
        return reference

    def index(self, repository, databases):
        """A multi-platform image with an apk database per platform; returns its reference and manifests."""
        manifests = {}
        for platform, database in databases.items():
            reference = self.put(repository, {"mediaType": "application/vnd.oci.image.manifest.v1+json", "platform": platform})
            self.databases[(reference, platform)] = database
            manifests[platform] = reference
        entries = [{"digest": ref.split("@")[1], "platform": dict(zip(("os", "architecture"), platform.split("/")))}
                   for platform, ref in manifests.items()]
        # buildx adds an attestation manifest per platform, under an unknown platform.
        entries.append({"digest": "sha256:" + "e" * 64, "platform": {"os": "unknown", "architecture": "unknown"}})
        index = {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": entries}
        return self.put(repository, index), manifests

    def attest(self, repository, subject, statements, *, signed=True, about=None, predicate_type=base_vex.OPENVEX,
               last_updated="2026-05-12T16:01:54Z"):
        """An OpenVEX attestation of `subject` in DHI's layout: a signed manifest whose one layer is the statement."""
        about = about or subject.split("@")[1]
        layer = json.dumps({"_type": "https://in-toto.io/Statement/v0.1", "predicateType": predicate_type,
                            "subject": [{"name": "pkg:docker/dhi/node", "digest": {"sha256": about.removeprefix("sha256:")}}],
                            "predicate": {"@context": base_vex.OPENVEX, "@id": f"https://scout.docker.com/public/vex-{len(self.content)}",
                                          "author": "Docker Hardened Images <dhi@docker.com>",
                                          "timestamp": last_updated, "last_updated": last_updated,
                                          "statements": statements}}).encode()
        layer_reference = self.put(repository, layer)
        manifest = self.put(repository, {"layers": [{"mediaType": base_vex.IN_TOTO, "digest": layer_reference.split("@")[1]}]})
        if signed:
            self.signed.add(manifest)
        self.referrer_lists.setdefault(subject, []).append({
            "artifactType": base_vex.IN_TOTO, "digest": manifest.split("@")[1],
            "annotations": {base_vex.PREDICATE_TYPE_ANNOTATION: predicate_type}})
        return manifest

    # The Registry interface base_vex.py calls. A tag next to the digest is ignored, as registries do.
    def manifest(self, reference):
        return json.loads(self.content[untagged(reference)])

    def manifest_bytes(self, reference):
        return self.content[untagged(reference)]

    def referrers(self, reference):
        return self.referrer_lists.get(reference, [])

    def blob(self, reference):
        return self.content[reference]

    def verify(self, reference):
        if reference not in self.signed:
            raise ValueError(f"cosign verify failed: no signature for {reference}")
        return reference not in self.unlogged

    def package_db(self, reference, platform):
        self.pulled.append(reference)
        database = self.databases[(reference, platform)]
        return database if isinstance(database, dict) else {"apk": database, "dpkg": []}


class InheritTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.base, self.base_manifests = self.registry.index("dhi.io/node", {p: apk_db(**DHI_GIT) for p in PLATFORMS})
        self.base = self.base.replace("dhi.io/node@", "dhi.io/node:24.22.3-alpine3.23-dev@")

    def image(self, databases):
        reference, _ = self.registry.index("ghcr.io/archestra-ai/ci/platform", databases)
        return reference

    def inherit(self, image):
        with contextlib.redirect_stdout(io.StringIO()):
            return base_vex.inherit(self.registry, image=image, subject_name="docker.io/archestra/platform", bases=[self.base])

    def attest_each_platform(self, statements, **options):
        for manifest in self.base_manifests.values():
            self.registry.attest("dhi.io/node", manifest, statements, **options)

    def test_carries_a_statement_over_for_the_package_builds_the_image_holds(self):
        self.attest_each_platform([statement()])
        image = self.image({p: apk_db(**DHI_GIT, musl=("1.2.5-r24", "Q1musl=")) for p in PLATFORMS})
        [carried] = self.inherit(image)
        self.assertEqual(carried["vulnerability"], {"name": "CVE-2025-66413"})
        self.assertEqual((carried["status"], carried["justification"]), ("not_affected", "vulnerable_code_not_present"))
        self.assertEqual(carried["products"], [{"@id": "pkg:oci/platform", "subcomponents": [{"@id": TEMPLATE}, {"@id": GIT}]}])
        self.assertIn("dhi.io/node:24.22.3-alpine3.23-dev", carried["status_notes"])
        # The project's own validation accepts it as it stands.
        vex.check_statement(carried, "carried")

    def test_carries_nothing_for_a_different_build_of_the_same_version(self):
        self.attest_each_platform([statement()])
        # Alpine's own git 2.52.1-r0 on arm64: same name and version, another build.
        alpine = {"git": ("2.52.1-r0", "Q1alpine-git="), "git-init-template": ("2.52.1-r0", "Q1alpine-template=")}
        image = self.image({"linux/amd64": apk_db(**DHI_GIT), "linux/arm64": apk_db(**alpine)})
        self.assertEqual(self.inherit(image), [])

    def test_pulls_a_base_only_when_the_image_may_hold_a_build_it_has_a_statement_about(self):
        self.attest_each_platform([statement()])
        other = {"curl": ("8.17.0-r1", "Q1curl=")}
        image = self.image({p: apk_db(**other) for p in PLATFORMS})
        self.assertEqual(self.inherit(image), [])
        bases = set(self.base_manifests.values())
        # The image's own platforms only: no statement names curl.
        self.assertFalse(bases & set(self.registry.pulled), self.registry.pulled)
        self.registry.pulled.clear()
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        self.assertEqual(len(self.inherit(image)), 1)
        self.assertEqual(bases & set(self.registry.pulled), bases)

    def test_carries_nothing_without_a_checksum_to_compare(self):
        self.attest_each_platform([statement(purls=(GIT,))])
        unsummed = "P:git\nV:2.52.1-r0\n\n"
        for platform, manifest in self.base_manifests.items():
            self.registry.databases[(manifest, platform)] = unsummed
        self.assertEqual(self.inherit(self.image({p: unsummed for p in PLATFORMS})), [])

    def test_carries_only_what_holds_for_the_package_wherever_it_runs(self):
        self.attest_each_platform([
            statement("CVE-2026-0001", justification="vulnerable_code_not_in_execute_path"),
            statement("CVE-2026-0002", justification="vulnerable_code_cannot_be_controlled_by_adversary"),
            statement("CVE-2026-0003", status="affected", justification=None),
            statement("CVE-2026-0004", status="under_investigation", justification=None),
            statement("CVE-2026-0005", justification="component_not_present", purls=(GIT,)),
            statement("CVE-2026-0006", status="fixed", justification=None, purls=(GIT,)),
            statement("DHI-2026-0007", purls=(GIT,)),  # not a CVE or GHSA
        ])
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        carried = {s["vulnerability"]["name"]: s for s in self.inherit(image)}
        self.assertEqual(sorted(carried), ["CVE-2026-0005", "CVE-2026-0006"])
        self.assertNotIn("justification", carried["CVE-2026-0006"])

    def test_a_package_the_image_lacks_is_left_out_of_the_statement(self):
        self.attest_each_platform([statement()])
        image = self.image({p: apk_db(git=DHI_GIT["git"]) for p in PLATFORMS})
        [carried] = self.inherit(image)
        self.assertEqual(carried["products"][0]["subcomponents"], [{"@id": GIT}])

    def test_the_newest_document_wins_a_package_both_name(self):
        self.attest_each_platform([statement(status="fixed", justification=None)], last_updated="2026-01-01T00:00:00Z")
        self.attest_each_platform([statement()], last_updated="2026-06-01T00:00:00Z")
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        self.assertEqual([s["status"] for s in self.inherit(image)], ["not_affected"])

    def test_refuses_an_attestation_docker_did_not_sign_or_that_is_about_another_image(self):
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        self.attest_each_platform([statement()], signed=False)
        with self.assertRaisesRegex(ValueError, "no signature"):
            self.inherit(image)
        self.registry.referrer_lists.clear()
        self.attest_each_platform([statement()], about="sha256:" + "1" * 64)
        with self.assertRaisesRegex(ValueError, "not about"):
            self.inherit(image)

    def test_skips_an_attestation_whose_signature_rekor_does_not_record(self):
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        for manifest in self.base_manifests.values():
            self.registry.unlogged.add(self.registry.attest("dhi.io/node", manifest, [statement()]))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            carried = base_vex.inherit(self.registry, image=image, subject_name="docker.io/archestra/platform", bases=[self.base])
        self.assertEqual(carried, [])
        self.assertIn("not in the transparency log", output.getvalue())

    def test_refuses_a_statement_whose_content_does_not_match_its_signed_digest(self):
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        manifest = self.registry.attest("dhi.io/node", self.base_manifests["linux/amd64"], [statement()])
        layer = f"dhi.io/node@{json.loads(self.registry.content[manifest])['layers'][0]['digest']}"
        self.registry.content[layer] = self.registry.content[layer].replace(b"not_affected", b"fixed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.inherit(image)

    def test_ignores_other_attestations_and_bases_without_the_platform(self):
        self.attest_each_platform([statement()], predicate_type="https://slsa.dev/provenance/v1")
        image = self.image({p: apk_db(**DHI_GIT) for p in PLATFORMS})
        self.assertEqual(self.inherit(image), [])
        amd64_only, _ = self.registry.index("dhi.io/node", {"linux/amd64": apk_db(**DHI_GIT)})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(base_vex.inherit(self.registry, image=image, subject_name="docker.io/archestra/platform",
                                              bases=[amd64_only]), [])


GLIBC = "pkg:deb/debian/glibc@2.41-12%2Bdeb13u4%2Bdhi1"
DOCKER_GLIBC = "2.41-12+deb13u4+dhi1"


def dpkg_record(package, version, maintainer="Docker Hardened Images <dhi@docker.com>", source="glibc"):
    return (f"Package: {package}\nStatus: install ok installed\nSource: {source}\nVersion: {version}\n"
            f"Maintainer: {maintainer}\nArchitecture: amd64\n\n")


def debian(status, records=()):
    """A Debian image's package databases: dpkg's status file and the base's status.d records."""
    return {"apk": "", "dpkg": [status, *records]}


class DebianInheritTests(unittest.TestCase):
    """Docker's Debian builds: matched by their +dhi version, by binary or source package name."""

    def setUp(self):
        self.registry = FakeRegistry()
        docker = debian("", [dpkg_record("libc6", DOCKER_GLIBC), dpkg_record("libc-bin", DOCKER_GLIBC)])
        self.base, self.base_manifests = self.registry.index("dhi.io/python", {p: docker for p in PLATFORMS})
        self.base = self.base.replace("dhi.io/python@", "dhi.io/python:3.14.10-debian13-dev@")

    def image(self, databases):
        reference, _ = self.registry.index("ghcr.io/archestra-ai/ci/platform", databases)
        return reference

    def inherit(self, image):
        with contextlib.redirect_stdout(io.StringIO()):
            return base_vex.inherit(self.registry, image=image, subject_name="docker.io/archestra/platform", bases=[self.base])

    def attest_each_platform(self, statements):
        for manifest in self.base_manifests.values():
            self.registry.attest("dhi.io/python", manifest, statements)

    def glibc(self, justification="inline_mitigations_already_exist"):
        return statement("CVE-2026-19499", justification=justification, purls=(GLIBC,))

    def test_carries_a_statement_for_dockers_build_the_image_still_holds(self):
        self.attest_each_platform([self.glibc()])
        image = self.image({p: debian(dpkg_record("libc6", DOCKER_GLIBC)) for p in PLATFORMS})
        [carried] = self.inherit(image)
        self.assertEqual((carried["status"], carried["justification"]), ("not_affected", "inline_mitigations_already_exist"))
        self.assertEqual(carried["products"], [{"@id": "pkg:oci/platform", "subcomponents": [{"@id": GLIBC}]}])
        vex.check_statement(carried, "carried")

    def test_carries_nothing_once_apt_replaced_dockers_build(self):
        self.attest_each_platform([self.glibc()])
        # apt upgraded libc6 to Debian's build: dpkg's status says so, the base's status.d record does not.
        upgraded = debian(dpkg_record("libc6", "2.41-12+deb13u5", maintainer="GNU Libc Maintainers <debian-glibc@lists.debian.org>"),
                          [dpkg_record("libc6", DOCKER_GLIBC)])
        self.assertEqual(self.inherit(self.image({p: upgraded for p in PLATFORMS})), [])

    def test_carries_nothing_for_debians_own_build(self):
        # Without a +dhi version nothing proves which build it is.
        version = "2.41-12+deb13u4"
        statement_ = statement("CVE-2026-19499", purls=(f"pkg:deb/debian/glibc@{version.replace('+', '%2B')}",))
        own = debian(dpkg_record("libc6", version, maintainer="GNU Libc Maintainers <debian-glibc@lists.debian.org>"))
        for platform, manifest in self.base_manifests.items():
            self.registry.databases[(manifest, platform)] = own
        self.attest_each_platform([statement_])
        self.assertEqual(self.inherit(self.image({p: own for p in PLATFORMS})), [])

    def test_inline_mitigations_carry_over_only_for_dockers_builds(self):
        apk = {"git": ("2.52.1-r0", "Q1alpine-git=")}
        for platform, manifest in self.base_manifests.items():
            self.registry.databases[(manifest, platform)] = apk_db(**apk)
        self.attest_each_platform([statement(justification="inline_mitigations_already_exist", purls=(GIT,))])
        self.assertEqual(self.inherit(self.image({p: apk_db(**apk) for p in PLATFORMS})), [])



class DockerfileTests(unittest.TestCase):
    def test_lists_the_pinned_dhi_bases_and_refuses_an_unpinned_one(self):
        pin = "dhi.io/node:24-dev@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            dockerfile = Path(directory) / "Dockerfile"
            dockerfile.write_text(f"ARG BASE={pin}\nFROM golang:1.26@sha256:{'b' * 64} AS build\n"
                                  f"FROM dhi.io.example.test/node:24@sha256:{'c' * 64} AS lookalike\n"
                                  f"FROM ${{BASE}} AS runtime\nCOPY --from={pin} /lib /lib\nFROM runtime AS final\n")
            self.assertEqual(base_vex.dhi_bases([dockerfile]), [pin])
            dockerfile.write_text("FROM dhi.io/node:24-dev\n")
            with self.assertRaisesRegex(ValueError, "not pinned by digest"):
                base_vex.dhi_bases([dockerfile])

    def test_trusts_the_key_base_images_toml_names_for_dhi(self):
        self.assertTrue(base_vex.dhi_key().endswith("supply-chain-policy/trust/docker-hardened-images-2.pub"))


class MergeTests(unittest.TestCase):
    """vex.with_inherited: the project's own statements win over carried-over ones."""

    DOCUMENT = {"@context": vex.CONTEXT, "@id": "https://example.test/vex", "author": "Archestra",
                "timestamp": "2026-11-18T00:00:00Z", "version": 2, "statements": []}

    def carried(self, *purls):
        return {"vulnerability": {"name": "CVE-2025-66413"}, "status": "not_affected",
                "justification": "vulnerable_code_not_present", "impact_statement": "Docker Hardened Images: git lacks the code.",
                "timestamp": "2026-05-12T16:01:54Z",
                "products": [{"@id": "pkg:oci/platform", "subcomponents": [{"@id": p} for p in purls]}]}

    def own(self, subcomponents=None, product="pkg:oci/platform"):
        value = {"vulnerability": {"name": "CVE-2025-66413"}, "status": "affected", "timestamp": "2026-11-18T00:00:00Z",
                 "action_statement": "Upgrade.", "products": [{"@id": product}]}
        if subcomponents:
            value["products"][0]["subcomponents"] = [{"@id": p} for p in subcomponents]
        return value

    def merged(self, own, carried):
        return vex.with_inherited({**self.DOCUMENT, "statements": own}, carried)["statements"]

    def test_the_project_statement_for_a_package_wins(self):
        statements = self.merged([self.own([GIT])], [self.carried(GIT, TEMPLATE)])
        self.assertEqual([s["status"] for s in statements], ["affected", "not_affected"])
        self.assertEqual(statements[1]["products"][0]["subcomponents"], [{"@id": TEMPLATE}])

    def test_a_project_statement_for_every_package_drops_the_carried_one(self):
        self.assertEqual(len(self.merged([self.own()], [self.carried(GIT)])), 1)

    def test_another_image_or_vulnerability_does_not_interfere(self):
        self.assertEqual(len(self.merged([self.own(product="pkg:oci/agent-codex")], [self.carried(GIT)])), 2)


if __name__ == "__main__":
    unittest.main()
