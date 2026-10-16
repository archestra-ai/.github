"""Choose cache writers without overriding the runner's cache protocol/endpoint."""

import os
import re


def policy(scope, mode, write, event, ref, default_branch, driver, backend="gha", repository="", pr_head_repository=""):
    if driver not in {"docker", "docker-container"}:
        raise ValueError("driver must be docker or docker-container")
    if scope and not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", scope):
        raise ValueError("cache-scope must be a single alphanumeric/dot/dash/underscore key")
    if mode not in {"min", "max"}:
        raise ValueError("cache-mode must be min or max")
    if write not in {"auto", "true", "false"}:
        raise ValueError("cache-write must be auto, true, or false")
    if backend not in {"gha", "ghcr"}:
        raise ValueError("cache-backend must be gha or ghcr")
    if backend == "ghcr" and (not scope or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)):
        raise ValueError("ghcr requires cache-scope and a valid owner/repository")
    # Fork tokens cannot authenticate to the base repository's private package.
    # Keep the existing GHA branch boundary instead of sharing registry credentials.
    if backend == "ghcr" and event == "pull_request" and pr_head_repository != repository:
        backend = "gha"
    writer = write == "true" or (
        write == "auto"
        and bool(default_branch)
        and event in {"push", "workflow_dispatch"}
        and ref == f"refs/heads/{default_branch}"
    )
    # Deliberately omit version/url/token. GitHub selects v2; Ubicloud's runner
    # selects its local proxy protocol. Forcing version=2 bypasses that proxy.
    if backend == "ghcr":
        ref = f"ghcr.io/{repository.lower()}-buildkit-cache:{scope}"
        return {
            "cache-backend": backend,
            "cache-write": str(writer).lower(),
            "cache-from": f"type=registry,ref={ref}",
            "cache-to": (f"type=registry,ref={ref},mode={mode},compression=zstd,"
                         "compression-level=1,force-compression=true,oci-mediatypes=true,image-manifest=true") if writer else "",
        }
    return {
        "cache-backend": backend,
        "cache-write": str(writer).lower(),
        "cache-from": f"type=gha,scope={scope}" if scope else "",
        "cache-to": f"type=gha,scope={scope},mode={mode}" if scope and writer else "",
    }


if __name__ == "__main__":
    try:
        result = policy(
            os.environ["CACHE_SCOPE"], os.environ["CACHE_MODE"],
            os.environ["CACHE_WRITE"], os.environ["EVENT_NAME"],
            os.environ["GITHUB_REF"], os.environ["DEFAULT_BRANCH"],
            os.environ["BUILDER_DRIVER"], os.environ["CACHE_BACKEND"],
            os.environ["REPOSITORY"], os.environ["PR_HEAD_REPOSITORY"],
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in result.items():
            print(f"{key}={value}", file=output)
