#!/usr/bin/env python3
"""Check that release-publisher published a handed-off request, before the
caller finishes the release (publishes its GitHub release, starts follow-ups).

The caller's finishing workflow is started by Release Gate when the publisher
run succeeds, or by a schedule. Neither is trusted: everything here comes from
the request the caller's own release run kept (`release-request-<attempt>`),
the GitHub API and the public registries.

1. The publisher run for the request, by its run name
   `Publish <project> <version> [<request id>]`, is the publisher repository's
   `.github/workflows/publish.yml` on `main`, started by `repository_dispatch`
   from the project's release App (actor and triggering actor), and its
   latest attempt concluded `success`.
2. Every public `<name>:<tag>` resolves to the digest the release attested,
   and that digest carries a build provenance attestation from the source
   repository's signer workflow (`gh attestation verify`).
3. Every chart in the request, pulled from its OCI repository at the
   version, has the sha256 the release attested.

Any mismatch fails the step and nothing after it runs.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?")
REQUEST_ID = re.compile(r"\d+-\d+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
IMAGE = re.compile(r"[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+")
OCI = re.compile(r"oci://[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+")
BOT = re.compile(r"[A-Za-z0-9-]+\[bot\]")
SIGNER = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+/\.github/workflows/[A-Za-z0-9._-]+\.ya?ml")
PUBLISH_WORKFLOW = ".github/workflows/publish.yml"
MAX_PAGES = 10


def main(environment):
    publisher = environment.get("VERIFY_PUBLISHER_REPOSITORY") or "archestra-ai/release-publisher"
    requester = environment["VERIFY_REQUESTER"]
    signer_repository = environment["VERIFY_SIGNER_REPOSITORY"]
    signer_workflow = environment["VERIFY_SIGNER_WORKFLOW"]
    image_tag = environment.get("VERIFY_IMAGE_TAG") or "{version}"
    if not REPOSITORY.fullmatch(publisher) or not REPOSITORY.fullmatch(signer_repository):
        raise ValueError("Invalid repository")
    if not BOT.fullmatch(requester):
        raise ValueError("requester must be a GitHub App bot login")
    if not SIGNER.fullmatch(signer_workflow) or not signer_workflow.startswith(signer_repository + "/"):
        raise ValueError("signer-workflow must be <signer repository>/.github/workflows/<file>")
    if image_tag not in ("{version}", "v{version}"):
        raise ValueError("image-tag must be {version} or v{version}")
    request = load_request(Path(environment["VERIFY_REQUEST_FILE"]))
    if request.get("smoke"):
        raise ValueError("A dry run publishes nothing to finish")
    charts = chart_repositories(environment.get("VERIFY_CHART_REPOSITORIES", ""))
    missing = sorted(set(((request.get("charts") or {}).get("sha256") or {})) - set(charts))
    if missing:
        raise ValueError(f"No OCI repository given for chart(s) {', '.join(missing)}")

    run = publisher_run(publisher, request, requester)
    print(f"Publisher run {run['html_url']} (attempt {run['run_attempt']}) succeeded")
    tag = image_tag.format(version=request["version"])
    for name, digest in sorted(request["images"].items()):
        published = command("docker", "buildx", "imagetools", "inspect", f"{name}:{tag}",
                            "--format", "{{json .Manifest}}")
        actual = json.loads(published).get("digest")
        if actual != digest:
            raise ValueError(f"{name}:{tag} is {actual}, not the attested {digest}")
        command("gh", "attestation", "verify", f"oci://{name}@{digest}", "--repo", signer_repository,
                "--signer-workflow", signer_workflow, "--deny-self-hosted-runners",
                token=environment.get("VERIFY_ATTESTATION_TOKEN"))
        print(f"{name}:{tag} = {digest}, attested")
    for chart, sha256 in sorted(((request.get("charts") or {}).get("sha256") or {}).items()):
        with tempfile.TemporaryDirectory() as directory:
            command("helm", "pull", f"{charts[chart]}/{chart}", "--version", request["version"],
                    "--destination", directory)
            archive = Path(directory) / f"{chart}-{request['version']}.tgz"
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != sha256:
            raise ValueError(f"Chart {chart} {request['version']} is sha256:{actual}, not the attested sha256:{sha256}")
        print(f"Chart {chart} {request['version']} = sha256:{sha256}")
    output = environment.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"run-url={run['html_url']}\nversion={request['version']}\n")


def load_request(path):
    request = json.loads(path.read_text())
    if (not isinstance(request, dict) or not PROJECT.fullmatch(str(request.get("project")))
            or not VERSION.fullmatch(str(request.get("version")))
            or not REQUEST_ID.fullmatch(str(request.get("request_id")))):
        raise ValueError("The request file is not a release-publisher request")
    images = request.get("images")
    if (not isinstance(images, dict) or not images
            or not all(IMAGE.fullmatch(name) and isinstance(digest, str) and DIGEST.fullmatch(digest)
                       for name, digest in images.items())):
        raise ValueError('The request must hold images {"<public name>": "sha256:<digest>"}')
    charts = (request.get("charts") or {}).get("sha256") or {}
    if not all(PROJECT.fullmatch(name) and SHA256.fullmatch(str(sha)) for name, sha in charts.items()):
        raise ValueError("The request's charts must be {\"<chart>\": \"<sha256>\"}")
    return request


def chart_repositories(lines):
    """`<chart> oci://<repository>` lines."""
    charts = {}
    for line in lines.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or not PROJECT.fullmatch(parts[0]) or not OCI.fullmatch(parts[1]) or parts[0] in charts:
            raise ValueError(f"Invalid chart repository line: {line!r}")
        charts[parts[0]] = parts[1]
    return charts


def publisher_run(publisher, request, requester):
    title = f"Publish {request['project']} {request['version']} [{request['request_id']}]"
    for page in range(1, MAX_PAGES + 1):
        runs = json.loads(command(
            "gh", "api", f"repos/{publisher}/actions/workflows/publish.yml/runs"
            f"?event=repository_dispatch&branch=main&per_page=100&page={page}"))["workflow_runs"]
        for run in runs:
            if run.get("display_title") == title:
                check_run(run, publisher, requester)
                return run
        if len(runs) < 100:
            break
    raise ValueError(f"No publisher run named {title!r}")


def check_run(run, publisher, requester):
    checks = {
        "repository": (run.get("repository") or {}).get("full_name") == publisher,
        "workflow": run.get("path") == PUBLISH_WORKFLOW,
        "branch": run.get("head_branch") == "main",
        "event": run.get("event") == "repository_dispatch",
        "actor": (run.get("actor") or {}).get("login") == requester,
        "triggering actor": (run.get("triggering_actor") or {}).get("login") == requester,
    }
    wrong = [name for name, ok in checks.items() if not ok]
    if wrong:
        raise ValueError(f"The publisher run {run.get('html_url')} has the wrong {', '.join(wrong)}")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError(f"The publisher run {run.get('html_url')} is {run.get('status')} "
                         f"({run.get('conclusion') or 'no conclusion'}), not successful")


def command(*args, token=None):
    environment = None if token is None else {**os.environ, "GH_TOKEN": token}
    return subprocess.run(list(args), text=True, check=True, stdout=subprocess.PIPE, env=environment).stdout


if __name__ == "__main__":
    try:
        main(os.environ)
    except (KeyError, ValueError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"::error::release not verified: {error}")
