#!/usr/bin/env python3
"""Send a release-images request for this run and wait for its publisher run.

The request ID is <run id>-<run attempt>. The publisher accepts only
attestations made in that run attempt and charts from that run's artifacts,
and puts "[<request id>]" at the end of its run name, which is how this
script finds the run: the dispatch API returns no run ID.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
CHANNELS = ("stable", "newest")
FIND_ATTEMPTS = 40
FIND_INTERVAL = 6


def main(environment):
    request = build_request(environment)
    repository = environment["PUBLISHER_REPOSITORY"]
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid publisher repository")
    gh("api", "-X", "POST", f"repos/{repository}/dispatches", "--input", "-",
       stdin=json.dumps({"event_type": "publish-release-images", "client_payload": request}))
    run_id = find_run(repository, request["request_id"])
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    print(f"Publisher run: {url}")
    output = environment.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"run-url={url}\nrun-id={run_id}\n")
    # Exits non-zero, and so fails this step, when the publisher fails.
    gh("run", "watch", str(run_id), "--repo", repository, "--exit-status", "--interval", "20", capture=False)


def build_request(environment):
    project = environment["PUBLISH_PROJECT"]
    if not PROJECT.fullmatch(project):
        raise ValueError("Invalid project")
    version = environment["PUBLISH_VERSION"]
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid version")
    smoke = environment.get("PUBLISH_SMOKE", "false")
    if smoke not in ("true", "false"):
        raise ValueError("smoke must be true or false")
    gate = environment.get("PUBLISH_GATE", "false")
    if gate not in ("true", "false"):
        raise ValueError("gate must be true or false")
    latest = environment.get("PUBLISH_LATEST", "").split()
    if len(set(latest)) != len(latest) or not all(channel in CHANNELS for channel in latest):
        raise ValueError(f"latest must list channels from {CHANNELS}")
    images = json.loads(Path(environment["PUBLISH_IMAGES_FILE"]).read_text())
    if (not isinstance(images, dict) or not images
            or not all(isinstance(digest, str) and DIGEST.fullmatch(digest) for digest in images.values())):
        raise ValueError('images-file must hold {"<public name>": "sha256:<digest>"}')
    run_id = environment["GITHUB_RUN_ID"]
    attempt = environment["GITHUB_RUN_ATTEMPT"]
    request = {"project": project, "request_id": f"{run_id}-{attempt}", "version": version,
               "latest": latest, "images": images}
    charts = {}
    for line in environment.get("PUBLISH_CHARTS", "").splitlines():
        path = Path(line.strip())
        if not line.strip():
            continue
        suffix = f"-{version}.tgz"
        if not path.name.endswith(suffix) or not path.is_file():
            raise ValueError(f"{path} is not a <chart>{suffix} file")
        charts[path.name.removesuffix(suffix)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if charts:
        request["charts"] = {"run_id": int(run_id), "sha256": charts}
    assets = {}
    for line in environment.get("PUBLISH_ASSETS", "").splitlines():
        if not line.strip():
            continue
        path = Path(line.strip())
        if not path.is_file() or path.name in assets:
            raise ValueError(f"{path} is not a release asset file, or is listed twice")
        assets[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if assets:
        request["assets"] = {"run_id": int(run_id), "sha256": assets}
    if smoke == "true":
        request["smoke"] = True
    if gate == "true":
        request["gate"] = True
    return request


def find_run(repository, request_id):
    for attempt in range(FIND_ATTEMPTS):
        runs = json.loads(gh("api", f"repos/{repository}/actions/runs?event=repository_dispatch&per_page=50"))
        for run in runs["workflow_runs"]:
            if run["display_title"].endswith(f"[{request_id}]"):
                return run["id"]
        if attempt + 1 < FIND_ATTEMPTS:
            time.sleep(FIND_INTERVAL)
    raise ValueError("The release-images publisher did not start")


def gh(*args, stdin=None, capture=True):
    result = subprocess.run(["gh", *args], input=stdin, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout


if __name__ == "__main__":
    try:
        main(os.environ)
    except (KeyError, ValueError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"::error::release-images publication failed: {error}")
