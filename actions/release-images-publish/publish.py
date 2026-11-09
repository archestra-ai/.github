#!/usr/bin/env python3
"""Send a release-publisher request for this run and wait for its publisher run.

The request ID is <run id>-<run attempt>. The publisher accepts attestations
made in that run (this attempt or an earlier one) and charts from that run's
artifacts, and puts "[<request id>]" at the end of its run name, which is how
this script finds the run: the dispatch API returns no run ID.

A release can wait hours for its approval, and an App installation token
lasts one hour. Given the App's ID and private key (PUBLISH_APP_ID,
PUBLISH_APP_PRIVATE_KEY), the wait mints a new token, for the publisher
repository only, before the current one is 45 minutes old and after a failed
poll. Without them it polls with GH_TOKEN, so the wait ends when that token
expires. A failed poll (an expired token, a GitHub 5xx) is retried; only
MAX_POLL_FAILURES in a row fail the step.

PUBLISH_WAIT=false hands the request off instead: the step ends once the
publisher run exists, and the release waits for its approval in the publisher
(up to GitHub's 30 days) rather than in this job. The caller then finishes the
release in a separate workflow that checks the publisher run and the public
names (the release-publish-verify action). PUBLISH_REQUEST_FILE, when set,
receives the request as sent, for that check.
"""

import hashlib
import json
import os
import re
import base64
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
CHANNELS = ("stable", "newest")
FIND_ATTEMPTS = 40
FIND_INTERVAL = 6
POLL_INTERVAL = 30
MAX_POLL_FAILURES = 10
# Installation tokens last 60 minutes; replace one well before that.
TOKEN_REFRESH_AFTER = 45 * 60
GITHUB_API = "https://api.github.com"


def main(environment):
    request = build_request(environment)
    repository = environment["PUBLISHER_REPOSITORY"]
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid publisher repository")
    wait_for_run = environment.get("PUBLISH_WAIT", "true")
    if wait_for_run not in ("true", "false"):
        raise ValueError("wait must be true or false")
    if environment.get("PUBLISH_REQUEST_FILE"):
        Path(environment["PUBLISH_REQUEST_FILE"]).write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")
    gh("api", "-X", "POST", f"repos/{repository}/dispatches", "--input", "-",
       stdin=json.dumps({"event_type": "publish-release-images", "client_payload": request}))
    run_id = find_run(repository, request["request_id"])
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    print(f"Publisher run: {url}")
    output = environment.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"run-url={url}\nrun-id={run_id}\n")
    if wait_for_run == "false":
        print("Handed off: the release waits for its approval in the publisher run")
        return
    tokens = Tokens(environment, repository)
    try:
        wait(repository, run_id, tokens)
    finally:
        tokens.revoke()


def wait(repository, run_id, tokens):
    """Poll the publisher run until it completes; fail unless it succeeded."""
    failures = 0
    while True:
        try:
            run = json.loads(gh("api", f"repos/{repository}/actions/runs/{run_id}", token=tokens.current()))
            failures = 0
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, urllib.error.URLError) as error:
            failures += 1
            print(f"Polling the publisher run failed ({failures}/{MAX_POLL_FAILURES}): {error}")
            if failures >= MAX_POLL_FAILURES:
                raise ValueError(f"Could not read publisher run {run_id} {failures} times in a row") from error
            tokens.expire()
            time.sleep(POLL_INTERVAL)
            continue
        if run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise ValueError(f"The publisher run concluded {run.get('conclusion')}")
            print("The publisher run succeeded")
            return
        time.sleep(POLL_INTERVAL)


class Tokens:
    """The token for the publisher repository: GH_TOKEN at first, then, when
    the App's key is given, a new installation token before the current one
    is TOKEN_REFRESH_AFTER old, or after a failed poll."""

    def __init__(self, environment, repository):
        self.token = environment["GH_TOKEN"]
        self.app_id = environment.get("PUBLISH_APP_ID", "")
        self.private_key = environment.get("PUBLISH_APP_PRIVATE_KEY", "").replace("\\n", "\n")
        self.owner, self.name = repository.split("/")
        self.minted_at = time.time()
        self.minted = []

    def current(self):
        if self.app_id and self.private_key and time.time() - self.minted_at >= TOKEN_REFRESH_AFTER:
            self.refresh()
        return self.token

    def expire(self):
        self.minted_at = 0

    def refresh(self):
        jwt = self.jwt()
        installation = api("GET", f"/repos/{self.owner}/{self.name}/installation", f"Bearer {jwt}")
        token = api("POST", f"/app/installations/{installation['id']}/access_tokens", f"Bearer {jwt}",
                    {"repositories": [self.name], "permissions": {"contents": "write", "actions": "read"}})["token"]
        print(f"::add-mask::{token}")
        self.token, self.minted_at = token, time.time()
        self.minted.append(token)
        print("Minted a new publisher token")

    def revoke(self):
        for token in self.minted:
            try:
                api("DELETE", "/installation/token", f"token {token}")
            except (OSError, ValueError) as error:
                print(f"Could not revoke a publisher token: {error}")

    def jwt(self):
        now = int(time.time())
        header = encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = encode(json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.app_id}).encode())
        with tempfile.NamedTemporaryFile("w", dir=os.environ.get("RUNNER_TEMP")) as key:
            os.chmod(key.name, 0o600)
            key.write(self.private_key)
            key.flush()
            signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name],
                                       input=f"{header}.{claims}".encode(), capture_output=True, check=True).stdout
        return f"{header}.{claims}.{encode(signature)}"


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
    raise ValueError("The publisher did not start")


def gh(*args, stdin=None, token=None):
    environment = None if token is None else {**os.environ, "GH_TOKEN": token}
    result = subprocess.run(["gh", *args], input=stdin, text=True, check=True,
                            stdout=subprocess.PIPE, env=environment)
    return result.stdout


def api(method, path, authorization, body=None):
    request = urllib.request.Request(
        GITHUB_API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": authorization, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()
    return json.loads(content) if content else {}


def encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


if __name__ == "__main__":
    try:
        main(os.environ)
    except (KeyError, ValueError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"::error::publication failed: {error}")
