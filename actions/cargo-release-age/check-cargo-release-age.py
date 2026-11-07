#!/usr/bin/env python3
"""Reject newly locked registry crates published less than seven days ago.

Requires Python 3.11+. Existing base-lockfile versions are grandfathered.
This is a CI/manual check, not a Cargo resolver or a check of git dependencies.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref", required=True, help="Trusted base commit to compare against"
    )
    parser.add_argument(
        "--lockfile",
        action="append",
        default=[],
        help="Repository-relative Cargo.lock path",
    )
    parser.add_argument(
        "--all-lockfiles",
        action="store_true",
        help=(
            "Check every tracked Cargo.lock outside vendored and fixture directories. "
            "A lockfile the base lacks is new: every registry version in it is checked."
        ),
    )
    args = parser.parse_args()
    if not args.lockfile and not args.all_lockfiles:
        parser.error("pass --lockfile at least once, or --all-lockfiles")
    try:
        root = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        )
        lockfiles = args.lockfile
        if args.all_lockfiles:
            # The base must exist even when no lockfile is found in it.
            subprocess.run(
                ["git", "cat-file", "-e", f"{args.base_ref}^{{commit}}"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            lockfiles = sorted(set(lockfiles) | set(tracked_lockfiles(root)))
            if not lockfiles:
                print("No tracked Cargo.lock; nothing to check.")
                return 0
        now = datetime.now(UTC)
        failures = []
        for lockfile in lockfiles:
            if args.all_lockfiles and not exists_at(root, args.base_ref, lockfile):
                base = EMPTY_LOCKFILE
            else:
                base = subprocess.check_output(
                    ["git", "show", f"{args.base_ref}:{lockfile}"], cwd=root, text=True
                )
            current = (root / lockfile).read_text()
            failures.extend(
                f"{lockfile}: {failure}"
                for failure in check_versions(base, current, now)
            )
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Cannot verify Cargo release ages: {error}", file=sys.stderr)
        return 1
    print(
        "Cargo release age check passed (new registry versions must be at least 7 days old)."
    )
    return 0


def check_versions(base: str, current: str, now: datetime) -> list[str]:
    failures = []
    checksums = {
        (package["name"], package["version"], package.get("source")): package.get(
            "checksum"
        )
        for package in tomllib.loads(current)["package"]
    }
    for name, version, source in sorted(
        registry_versions(current) - registry_versions(base)
    ):
        if source != CRATES_IO:
            failures.append(
                f"{name}@{version}: cannot verify unsupported registry {source}"
            )
            continue
        published = publication_time(name, version)
        eligible = published + timedelta(days=7)
        if now < eligible:
            exception_until = SECURITY_RELEASE_AGE_EXCEPTIONS.get(
                (name, version, checksums[(name, version, source)])
            )
            if exception_until is not None and published <= now < exception_until:
                print(
                    f"{name}@{version}: approved security release-age exception "
                    f"until {exception_until.isoformat()} (exact crates.io checksum)"
                )
                continue
            failures.append(
                f"{name}@{version}: published {published.isoformat()}; eligible {eligible.isoformat()}"
            )
    return failures


def tracked_lockfiles(root: Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return [
        path
        for path in output.decode().split("\0")
        if path.split("/")[-1] == "Cargo.lock"
        and not IGNORED_DIRECTORIES.intersection(path.split("/")[:-1])
    ]


def exists_at(root: Path, ref: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"], cwd=root, capture_output=True
    )
    return result.returncode == 0


def registry_versions(lockfile: str) -> set[tuple[str, str, str]]:
    # Cargo records every resolved version, including transitive dependencies.
    return {
        (package["name"], package["version"], package["source"])
        for package in tomllib.loads(lockfile)["package"]
        if package.get("source", "").startswith(("registry+", "sparse+"))
    }


def publication_time(name: str, version: str) -> datetime:
    request = Request(
        f"https://crates.io/api/v1/crates/{quote(name, safe='')}/{quote(version, safe='')}",
        headers={
            "User-Agent": "cargo-release-age-check (https://github.com/archestra-ai)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        metadata = json.load(response)["version"]
    if metadata["num"] != version or metadata["crate"] != name:
        raise ValueError(f"Unexpected registry metadata for {name}@{version}")
    published = datetime.fromisoformat(metadata["created_at"])
    if published.utcoffset() is None:
        raise ValueError(f"Missing publication timezone for {name}@{version}")
    return published


CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
EMPTY_LOCKFILE = "version = 4\npackage = []\n"
# Vendored code and test fixtures, as in the supply-chain policy checker.
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

# Trusted, exact-artifact exceptions for the emSign distrust security updates.
# No consumer-supplied bypass. Each expires at the release's seven-day boundary;
# publication metadata must still be available and the registry must be crates.io.
# RUSTSEC-2026-0382, RUSTSEC-2026-0383, RUSTSEC-2026-0381 respectively.
SECURITY_RELEASE_AGE_EXCEPTIONS = {
    (
        "rustls-webpki",
        "0.103.16",
        "4443b1b6d39d7c663139942cc4558262562d2c84123bb6282ef177009ca8b281",
    ): datetime.fromisoformat("2026-10-27T08:58:28.008618+00:00"),
    (
        "webpki-root-certs",
        "1.0.10",
        "58ec80e73fd8baf57bf0d9615554c75348bda990d6d5bbeab964dd068319fecb",
    ): datetime.fromisoformat("2026-10-26T20:11:38.525787+00:00"),
    (
        "webpki-roots",
        "1.0.10",
        "1c1c8e20c8a38fc7d65abfe74eb7280e4aa667e8eec8bc84996d2874810748d9",
    ): datetime.fromisoformat("2026-10-26T20:11:32.333071+00:00"),
}


if __name__ == "__main__":
    raise SystemExit(main())
