#!/usr/bin/env python3
"""Job-local routing for a fixed, end-to-end TLS IAP destination. No PR checkout."""
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

HOST = "frontend.archestra.dev"
PORT = 18443
MARKER = "archestra-runtime-review-iap"
ORG_ID = "216022683"
RULE = ["-d", "127.0.0.2/32", "-p", "tcp", "--dport", "443", "-m", "comment",
        "--comment", MARKER, "-j", "REDIRECT", "--to-ports", str(PORT)]


def check():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pr = event.get("pull_request", {})
    # Any repository of the archestra-ai organization, by its immutable owner id;
    # the pull request must come from a branch of that same repository.
    repo_id = os.environ.get("GITHUB_REPOSITORY_ID", "")
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("GITHUB_REPOSITORY_OWNER_ID") == ORG_ID
            and os.environ.get("GITHUB_REPOSITORY", "").startswith("archestra-ai/")
            and os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
            and repo_id.isdigit()
            and str(pr.get("head", {}).get("repo", {}).get("id")) == repo_id
            and str(pr.get("base", {}).get("repo", {}).get("id")) == repo_id):
        raise RuntimeError("Private staging access requires a same-repository pull request in an archestra-ai repository on a Linux Actions runner.")
    url = urlsplit(os.environ.get("GATEWAY_URL", ""))
    if not (url.scheme == "https" and url.hostname == HOST and url.port in (None, 443)
            and not url.username and not url.password and not url.query and not url.fragment
            and re.fullmatch(r"/v1/mcp/[0-9a-fA-F-]{36}", url.path)):
        raise RuntimeError("Private review access accepts only the existing frontend.archestra.dev MCP gateway.")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def stop(directory):
    state = directory / "pid"
    if not state.exists():
        return
    # Only this action's marked host entry and exact NAT rule are removed.
    subprocess.run(["sudo", "iptables", "-t", "nat", "-D", "OUTPUT", *RULE],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    run("sudo", "sed", "-i", f"/ # {MARKER}$/d", "/etc/hosts")
    pid = int(state.read_text())
    try:
        # Verify ownership before signaling, in case the process exited and its PID was reused.
        command = run("ps", "-p", str(pid), "-o", "args=", capture_output=True, text=True).stdout
        if "start-iap-tunnel staging-vpn 8443" in command and f"127.0.0.1:{PORT}" in command:
            os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, subprocess.CalledProcessError):
        pass
    state.unlink()


def start(directory):
    if (directory / "pid").exists():
        raise RuntimeError("A review tunnel is already registered; refusing to reuse it.")
    for line in Path("/etc/hosts").read_text().splitlines():
        if HOST in line.split("#", 1)[0].split()[1:]:
            raise RuntimeError("The staging host already has a local override; refusing to replace it.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    directory.mkdir(mode=0o700)
    try:
        with (directory / "tunnel.log").open("xb") as log:
            process = subprocess.Popen([
                "gcloud", "compute", "start-iap-tunnel", "staging-vpn", "8443",
                "--project=friendly-path-465518-r6", "--zone=us-central1-a",
                f"--local-host-port=127.0.0.1:{PORT}", "--quiet",
            ], stdout=log, stderr=log, start_new_session=True)
        (directory / "pid").write_text(str(process.pid))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("IAP tunnel exited; check the review identity and port 8443 grants.")
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("IAP tunnel startup timed out.")
        # curl keeps the real hostname for SNI and certificate verification.
        health = ["curl", "--fail", "--silent", "--show-error", "--noproxy", "*",
                  "--max-time", "20", "--output", "/dev/null"]
        run(*health, "--connect-to", f"{HOST}:443:127.0.0.1:{PORT}", f"https://{HOST}/auth/sign-in")
        run("sudo", "tee", "-a", "/etc/hosts", input=f"\n127.0.0.2 {HOST} # {MARKER}\n",
            text=True, stdout=subprocess.DEVNULL)
        run("sudo", "iptables", "-t", "nat", "-A", "OUTPUT", *RULE)
        run(*health, f"https://{HOST}/auth/sign-in")
        print("Private staging HTTPS verified through IAP; certificate checks remain enabled.")
    except BaseException:
        stop(directory)
        raise


if __name__ == "__main__":
    os.umask(0o077)
    try:
        operation = sys.argv[1]
        if operation == "check":
            check()
        elif operation in ("start", "stop"):
            directory = Path(os.environ["RUNNER_TEMP"]) / MARKER
            start(directory) if operation == "start" else stop(directory)
        else:
            raise RuntimeError("Expected check, start or stop.")
    except Exception as error:
        raise SystemExit(f"::error::{error}") from None
