#!/usr/bin/env python3
"""Builds the image benchmark predicate from a Dockle report.

  benchmark.py <dockle.json> <predicate.json> --image REF --key KEY --version V
               --started T --finished T [--waivers FILE] [--on-failure fail|record]

Reads Dockle's JSON report (the CIS Docker Benchmark's image checks and
Dockle's own) into the image benchmark predicate
(https://archestra.ai/attestations/image-benchmark/v1): each check the image
did not pass, its level, and the waiver that covers it. A waiver names the
images it covers by key (the image's key in the release request, such as
docker.io/archestra/platform:slim, or a glob such as */agent-*), the check,
and the reason; with `alerts` (globs), it covers the finding only while each
of the finding's alerts matches one of them, so a new alert fails again. With
--on-failure fail (the default) a FATAL or WARN finding no waiver covers
exits 2. INFO findings are recorded.
"""

import argparse
import fnmatch
import json
import re
import sys
from collections import Counter

DOCKLE_URI = "https://github.com/goodwithtech/dockle"
# Levels Dockle reports a check at, most severe first; FAILING_LEVELS fail.
BENCHMARK_LEVELS = ("FATAL", "WARN", "INFO", "SKIP")
FAILING_LEVELS = ("FATAL", "WARN")
CHECK = re.compile(r"^(CIS-DI|DKL-DI|DKL-LI)-[0-9]{4}$")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("predicate")
    parser.add_argument("--image", required=True)
    parser.add_argument("--key", required=True, help="The image's key in the release request, which waivers match")
    parser.add_argument("--version", required=True)
    parser.add_argument("--waivers", default="")
    parser.add_argument("--started", required=True)
    parser.add_argument("--finished", required=True)
    parser.add_argument("--on-failure", choices=["fail", "record"], default="fail")
    return benchmark_predicate(parser.parse_args(argv))


def benchmark_predicate(args):
    with open(args.report, encoding="utf-8") as report:
        result = json.load(report)
    if not isinstance(result.get("summary"), dict) or not isinstance(result.get("details"), list):
        raise ValueError(f"{args.report} is not a Dockle JSON report")
    waivers = [waiver for waiver in load_waivers(args.waivers) if any(fnmatch.fnmatchcase(args.key, image) for image in waiver["images"])]
    findings, used = [], set()
    for detail in result["details"]:
        code, level = detail.get("code", ""), detail.get("level", "")
        if not CHECK.match(code) or level not in BENCHMARK_LEVELS:
            raise ValueError(f"{args.report}: unexpected check {code!r} at level {level!r}")
        finding = {"check": code, "title": detail.get("title", ""), "level": level,
                   "alerts": sorted(str(alert) for alert in detail.get("alerts") or [])}
        waiver = next((w for w in waivers if w["check"] == code and covers(w, finding["alerts"])), None)
        if waiver:
            finding["waiver"] = waiver["reason"]
            used.add(code)
        findings.append(finding)
    findings.sort(key=lambda f: (BENCHMARK_LEVELS.index(f["level"]), f["check"]))
    failing = [f for f in findings if f["level"] in FAILING_LEVELS and "waiver" not in f]
    counts = Counter(f["level"] for f in findings)
    predicate = {
        "scanner": {"uri": DOCKLE_URI, "version": args.version,
                    "checks": f"{DOCKLE_URI}/blob/v{args.version}/CHECKPOINT.md"},
        "image": args.image,
        "policy": {"failOn": list(FAILING_LEVELS) if args.on_failure == "fail" else [],
                   "waivers": [{key: w[key] for key in ("check", "reason", "alerts") if key in w} for w in waivers]},
        "result": {
            "passed": int(result["summary"].get("pass", 0)),
            **{level.lower(): counts[level] for level in BENCHMARK_LEVELS},
            "waived": sum(1 for f in findings if "waiver" in f),
            "findings": findings,
        },
        "metadata": {"scanStartedOn": args.started, "scanFinishedOn": args.finished},
    }
    write_json(args.predicate, predicate)
    print(f"Benchmark of {args.image} ({args.key}): {predicate['result']['passed']} passed, "
          + ", ".join(f"{counts[level]} {level}" for level in BENCHMARK_LEVELS)
          + f", {predicate['result']['waived']} waived (Dockle {args.version})")
    for waiver in waivers:
        if waiver["check"] not in used:
            print(f"::warning::{args.key} passes {waiver['check']}; remove its waiver")
    if failing and args.on_failure == "fail":
        for finding in failing:
            print(f"::error::{args.image} fails {finding['check']} ({finding['level']}): {finding['title']}: "
                  + "; ".join(finding["alerts"]))
        print("::error::Fix the image, or waive the check with a reason; nothing was attested.")
        return 2
    return 0


def covers(waiver, alerts):
    """A waiver with alert globs covers a finding only if each alert matches one."""
    patterns = waiver.get("alerts")
    return patterns is None or all(any(fnmatch.fnmatchcase(alert, pattern) for pattern in patterns) for alert in alerts)


def load_waivers(path):
    """The project's waivers: {"waivers": [{"images": [glob, ...], "check": code, "reason": text,
    "alerts": [glob, ...] (optional)}]}."""
    if not path:
        return []
    with open(path, encoding="utf-8") as file:
        document = json.load(file)
    waivers = document.get("waivers") if isinstance(document, dict) else None
    if not isinstance(waivers, list) or set(document) != {"waivers"}:
        raise ValueError(f"{path} must be an object with only a waivers list")
    for index, waiver in enumerate(waivers):
        where = f"{path}: waivers[{index}]"
        if not isinstance(waiver, dict) or not {"images", "check", "reason"} <= set(waiver) <= {"images", "check", "reason", "alerts"}:
            raise ValueError(f"{where} must have images, check and reason, and optionally alerts")
        if "alerts" in waiver and (not isinstance(waiver["alerts"], list) or not waiver["alerts"]
                                   or not all(isinstance(alert, str) and alert for alert in waiver["alerts"])):
            raise ValueError(f"{where}: alerts must be a non-empty list of globs")
        if not isinstance(waiver["images"], list) or not waiver["images"] or \
                not all(isinstance(image, str) and image for image in waiver["images"]):
            raise ValueError(f"{where}: images must be a non-empty list of image keys or globs")
        if not isinstance(waiver["check"], str) or not CHECK.match(waiver["check"]):
            raise ValueError(f"{where}: check must be a Dockle check code such as CIS-DI-0001")
        if not isinstance(waiver["reason"], str) or len(waiver["reason"].strip()) < 10:
            raise ValueError(f"{where}: reason must say why the check does not apply")
    return waivers


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.exit(f"::error::image-benchmark: {error}")
