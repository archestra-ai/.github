#!/usr/bin/env python3
"""Finish a release that its release run handed off to release-publisher.

A release run sends its request with release-publish.yml `wait: false` and
ends; the release waits for its approval in the publisher, for up to 30 days.
The project's finalize workflow (release-finalize.yml) then publishes the
GitHub release once the publisher run has succeeded. Release Gate dispatches it
with `request_id`, and a schedule sweeps for drafts it missed.

Neither trigger is trusted: `request` reads everything from this repository's
own release run and its kept `release-request-<attempt>` artifact, and the
caller runs release-publish-verify before `publish` writes anything.

  request  FINALIZE_REQUEST_ID, FINALIZE_BRANCH, FINALIZE_REQUEST_DIR
           Resolve the request; outputs run-id, attempt, version, tag-name,
           request-file, and publish=true while its release is still a draft.
  sweep    FINALIZE_PUBLISHER_TOKEN, FINALIZE_WORKFLOW
           Dispatch FINALIZE_WORKFLOW (with request_id) on the branch of each
           draft release whose newest publisher run succeeded.
  publish  FINALIZE_TAG_NAME, FINALIZE_VERSION, FINALIZE_REQUEST_FILE,
           FINALIZE_FILES (newline-separated), FINALIZE_LATEST
           Upload the files to the draft release and publish it. A file the
           request names (a chart or a release asset) must match its sha256.

Every operation also reads GH_TOKEN (this repository), GH_REPO,
FINALIZE_PROJECT, FINALIZE_RELEASE_WORKFLOW, FINALIZE_RELEASE_BRANCHES (a
regular expression), FINALIZE_TAG_TEMPLATE ({version} placeholder),
FINALIZE_REQUESTER and FINALIZE_PUBLISHER_REPOSITORY.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REQUEST_ID = re.compile(r"([1-9][0-9]*)-([1-9][0-9]*)")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
REPOSITORY = re.compile(r"archestra-ai/[A-Za-z0-9._-]+")
WORKFLOW_FILE = re.compile(r"[A-Za-z0-9._-]+\.ya?ml")


def main(environment):
    config = Config(environment)
    operation = sys.argv[1] if len(sys.argv) > 1 else ""
    if operation == "request":
        found = resolve(config, environment.get("FINALIZE_BRANCH", ""), environment.get("FINALIZE_REQUEST_ID", ""),
                        Path(environment["FINALIZE_REQUEST_DIR"]))
        outputs = {"run-id": found["run_id"], "attempt": found["attempt"], "version": found["version"],
                   "tag-name": found["tag_name"], "request-file": found["request_file"],
                   "publish": "true" if found["publish"] else "false"}
        if not found["publish"]:
            print(f"{found['tag_name']} is already published")
    elif operation == "sweep":
        workflow = environment.get("FINALIZE_WORKFLOW", "")
        if not WORKFLOW_FILE.fullmatch(workflow):
            raise ValueError("finalize-workflow must be a workflow file name, for example release-finalize.yml")
        found = sweep(config, environment["FINALIZE_PUBLISHER_TOKEN"])
        for branch, request_id in found:
            command("gh", "workflow", "run", workflow, "--repo", config.repository, "--ref", branch,
                    "-f", f"request_id={request_id}")
            print(f"Dispatched {workflow} on {branch} for request {request_id}")
        if not found:
            print("No approved draft releases")
        outputs = {"dispatched": len(found)}
    elif operation == "publish":
        files = [Path(line) for line in environment.get("FINALIZE_FILES", "").splitlines() if line.strip()]
        published = publish(config, environment["FINALIZE_TAG_NAME"], environment["FINALIZE_VERSION"],
                            Path(environment["FINALIZE_REQUEST_FILE"]), files,
                            latest=environment.get("FINALIZE_LATEST", "true") == "true")
        outputs = {"published": "true" if published else "false"}
    else:
        raise ValueError("Usage: finalize.py request|sweep|publish")
    with open(environment["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")
    return 0


class Config:
    """The project's release settings, checked once."""

    def __init__(self, environment):
        self.repository = environment["GH_REPO"]
        self.project = environment["FINALIZE_PROJECT"]
        self.release_workflow = environment["FINALIZE_RELEASE_WORKFLOW"]
        self.branches = re.compile(environment.get("FINALIZE_RELEASE_BRANCHES") or "main")
        self.tag_template = environment.get("FINALIZE_TAG_TEMPLATE") or "v{version}"
        self.requester = environment["FINALIZE_REQUESTER"]
        self.publisher = environment.get("FINALIZE_PUBLISHER_REPOSITORY") or "archestra-ai/release-publisher"
        if not REPOSITORY.fullmatch(self.repository) or not REPOSITORY.fullmatch(self.publisher):
            raise ValueError("Repositories must be archestra-ai/<name>")
        if not re.fullmatch(r"\.github/workflows/" + WORKFLOW_FILE.pattern, self.release_workflow):
            raise ValueError("release-workflow must be .github/workflows/<file>")
        if self.tag_template.count("{version}") != 1:
            raise ValueError("tag-template must hold {version} once")
        if not re.fullmatch(r"[a-z0-9-]+", self.project) or not self.requester.endswith("[bot]"):
            raise ValueError("project must be a publisher project name and requester an App bot login")

    def tag(self, version):
        return self.tag_template.replace("{version}", version)

    def version(self, tag):
        prefix, suffix = self.tag_template.split("{version}")
        if tag.startswith(prefix) and tag.endswith(suffix):
            version = tag[len(prefix):len(tag) - len(suffix) if suffix else None]
            if VERSION.fullmatch(version):
                return version
        return None

    def publish_title(self):
        return re.compile(rf"Publish {re.escape(self.project)} (\S+) \[([1-9][0-9]*-[1-9][0-9]*)\]")


def resolve(config, branch, request_id, directory, request=None, fetch=None):
    """The handed-off request `request_id` of this repository's release run on
    `branch`, and whether its draft release still needs publishing."""
    request = request or api
    fetch = fetch or download
    match = REQUEST_ID.fullmatch(request_id or "")
    if not match:
        raise ValueError("request_id must be <run id>-<run attempt>")
    if not config.branches.fullmatch(branch):
        raise ValueError(f"Releases finish only on a release branch, not {branch}")
    run_id, attempt = int(match.group(1)), int(match.group(2))
    run = request(f"repos/{config.repository}/actions/runs/{run_id}")
    if (run.get("path") != config.release_workflow or run.get("head_branch") != branch
            or run.get("event") not in ("push", "workflow_dispatch")
            or run["repository"]["full_name"] != config.repository or run.get("run_attempt", 0) < attempt):
        raise ValueError(f"Run {run_id} is not a {config.release_workflow} run on {branch}")
    fetch(config.repository, run_id, f"release-request-{attempt}", directory)
    payload = json.loads((Path(directory) / "request.json").read_text(encoding="utf-8"))
    version = payload.get("version", "")
    if (payload.get("project") != config.project or payload.get("request_id") != request_id
            or not VERSION.fullmatch(version) or payload.get("smoke")):
        raise ValueError(f"The kept request of run {run_id} attempt {attempt} is not a {config.project} release")
    tag = config.tag(version)
    # Draft releases have no tag lookup, so read the list.
    release = {item["tag_name"]: item for item in request(f"repos/{config.repository}/releases?per_page=100")}.get(tag)
    if release is None:
        raise ValueError(f"No release {tag}")
    return {"run_id": run_id, "attempt": attempt, "version": version, "tag_name": tag,
            "publish": bool(release.get("draft")), "request_file": str(Path(directory) / "request.json")}


def sweep(config, publisher_token, request=None):
    """(branch, request id) for each draft release whose newest publisher run
    succeeded, oldest version first."""
    request = request or api
    drafts = {config.version(item["tag_name"]) for item in request(f"repos/{config.repository}/releases?per_page=100")
              if item.get("draft")} - {None}
    if not drafts:
        return []
    title_pattern = config.publish_title()
    runs = request(f"repos/{config.publisher}/actions/workflows/publish.yml/runs"
                   "?event=repository_dispatch&branch=main&per_page=100", token=publisher_token)["workflow_runs"]
    newest = {}
    for run in runs:
        title = title_pattern.fullmatch(run.get("display_title") or "")
        if not title or title.group(1) not in drafts or (run.get("triggering_actor") or {}).get("login") != config.requester:
            continue
        # The newest request for a version decides: an older success must not
        # finish a version whose latest request was rejected or failed.
        if title.group(1) not in newest or run["created_at"] > newest[title.group(1)]["created_at"]:
            newest[title.group(1)] = run
    found = []
    for version, run in sorted(newest.items()):
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        request_id = title_pattern.fullmatch(run["display_title"]).group(2)
        source = request(f"repos/{config.repository}/actions/runs/{request_id.split('-')[0]}")
        if source.get("path") == config.release_workflow and config.branches.fullmatch(source.get("head_branch") or ""):
            found.append((source["head_branch"], request_id))
    return found


def publish(config, tag, version, request_file, files, latest=True, run=None):
    """Upload `files` to the draft release `tag` and publish it. False when it
    was already published (a retry, or a second dispatch)."""
    run = run or command
    if config.tag(version) != tag or not VERSION.fullmatch(version):
        raise ValueError(f"{tag} is not the tag of {version}")
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    expected = {**{f"{name}-{version}.tgz": sha for name, sha in (payload.get("charts") or {}).get("sha256", {}).items()},
                **(payload.get("assets") or {}).get("sha256", {})}
    for path in files:
        if not path.is_file():
            raise ValueError(f"{path} is not a file")
        if path.name in expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected[path.name]:
            raise ValueError(f"{path.name} is not the file the request named")
    # An immutable release cannot change once published, so a retry stops here.
    if json.loads(run("gh", "release", "view", tag, "--repo", config.repository, "--json", "isDraft"))["isDraft"] is not True:
        print(f"{tag} is already published")
        return False
    if files:
        run("gh", "release", "upload", tag, *map(str, files), "--repo", config.repository, "--clobber")
    if "-" in version:
        flags = ["--prerelease", "--latest=false"]
    else:
        flags = ["--prerelease=false", "--latest" if latest else "--latest=false"]
    run("gh", "release", "edit", tag, "--repo", config.repository, "--draft=false", *flags)
    print(f"Published {tag} with {len(files)} file(s)")
    return True


def command(*args, token=None):
    environment = None if token is None else {**os.environ, "GH_TOKEN": token}
    return subprocess.check_output(args, text=True, env=environment).strip()


def api(path, token=None):
    return json.loads(command("gh", "api", path, token=token))


def download(repository, run_id, name, directory):
    command("gh", "run", "download", str(run_id), "--repo", repository, "--name", name, "--dir", str(directory))


if __name__ == "__main__":
    try:
        sys.exit(main(os.environ))
    except (KeyError, OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        sys.exit(f"::error::release-finalize: {error}")
