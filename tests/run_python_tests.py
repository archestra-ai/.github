#!/usr/bin/env python3
"""Run every Python test in this repository with one command.

    python3 tests/run_python_tests.py                 # every test directory
    python3 tests/run_python_tests.py actions/vercel-deploy
    python3 tests/run_python_tests.py -- -v           # verbose unittest output

Each directory that holds `test_*.py` files runs as its own
`python3 -m unittest discover` process from the repository root. The test
directories are not packages and several load their module under test by file
name, so one process per directory keeps same-named modules (`check.py`,
`verify.py`, ...) from colliding. Arguments after `--` go to every
`unittest` run. Environment variables reach the tests unchanged
(for example `RUN_NETWORK_TESTS=1`). Exits non-zero if any directory fails
or finds no tests.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = "test_*.py"
SKIP_DIRS = {".git", ".claude", ".venv", "venv", "node_modules", "__pycache__"}


def main(argv: list[str]) -> int:
    if "--" in argv:
        split = argv.index("--")
        paths, unittest_args = argv[:split], argv[split + 1 :]
    else:
        paths, unittest_args = argv, []

    directories = test_directories([ROOT / p for p in paths] if paths else [ROOT])
    if not directories:
        print("No test directories found.", file=sys.stderr)
        return 1

    total = skipped = 0
    failed = []
    for directory in directories:
        name = directory.relative_to(ROOT).as_posix() or "."
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", name, "-p", PATTERN]
            + unittest_args,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ran = count(r"^Ran (\d+) tests?", result.stdout)
        skips = count(r"skipped=(\d+)", result.stdout)
        total += ran
        skipped += skips
        ok = result.returncode == 0 and ran > 0
        if not ok or unittest_args:
            print(result.stdout, end="")
        status = "ok" if ok else "FAILED"
        print(f"{status:6} {name}: {ran} tests, {skips} skipped", flush=True)
        if not ok:
            failed.append(name)

    print(f"\n{total} tests in {len(directories)} directories, {skipped} skipped")
    if failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


def test_directories(starts: list[Path]) -> list[Path]:
    """Directories holding test files, minus those a parent's discovery reaches.

    `unittest discover` recurses only into packages (directories with an
    `__init__.py`), so a directory runs on its own unless every directory
    between it and another test directory is a package.
    """
    found = set()
    for start in starts:
        if start.is_file():
            start = start.parent
        for test_file in start.rglob(PATTERN):
            relative = test_file.relative_to(ROOT).parts
            if not SKIP_DIRS.intersection(relative):
                found.add(test_file.parent)

    def reached_by_parent(directory: Path) -> bool:
        while (directory / "__init__.py").exists() and directory.parent != directory:
            directory = directory.parent
            if directory in found:
                return True
        return False

    return sorted(d for d in found if not reached_by_parent(d))


def count(pattern: str, output: str) -> int:
    matches = re.findall(pattern, output, re.MULTILINE)
    return int(matches[-1]) if matches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
