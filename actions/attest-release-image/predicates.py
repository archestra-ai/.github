#!/usr/bin/env python3
"""Builds the scan predicates attest-release-image signs.

  secrets <trufflehog.jsonl> <predicate.json> --image REF --version V
          --started T --finished T [--on-verified fail|record]
      Counts trufflehog's findings by verification result and detector. The
      predicate never holds a secret, its location or its redacted form. With
      --on-verified fail (the default) a verified finding exits 2, so nothing
      is attested and the release stops.

  malware <clamscan.log> <predicate.json> --image REF --version V
          --database V --started T --finished T [--on-detection fail|record]
      Reads clamscan's output (-i: only detections, then its summary) into
      the malware scan predicate: file counts, the signature database, and
      each detection's signature and path. With --on-detection fail (the
      default) a detection exits 2, so nothing is attested and the release
      stops.

  vulnerabilities <report.json> <predicate.json> --scanner-uri URI
          --scanner-version V --started T --finished T
      Wraps a scanner's JSON report in the cosign vulnerability predicate
      (https://cosign.sigstore.dev/attestation/vuln/v1).

  vex <document> <predicate.json> --image REF --subject-name NAME
      Selects the statements of the project's OpenVEX document whose product
      is this image (`pkg:oci/<last segment of the public name>`, no version)
      and writes them as an OpenVEX document about the image's digest under its
      public name (https://openvex.dev/ns/v0.2.0). Exits 3, writing nothing,
      when no statement names the image; an invalid statement exits 1.

  subjects <image> <subject name> <subjects file>
      Writes the attestation subject for the image's digest under its public
      name, in `sha256sum` format.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter

SECRET_SCAN_TYPE = "https://archestra.ai/attestations/secret-scan/v1"
MALWARE_SCAN_TYPE = "https://archestra.ai/attestations/malware-scan/v1"
CLAMAV_URI = "https://www.clamav.net"
DETECTION = re.compile(r"^(?P<path>/.*): (?P<signature>\S+) FOUND$")
SCAN_ERROR = re.compile(r"^(?P<path>/.*): (?P<reason>.+) ERROR$")
VULNERABILITY_TYPE = "https://cosign.sigstore.dev/attestation/vuln/v1"
TRUFFLEHOG_URI = "https://github.com/trufflesecurity/trufflehog"
# Exit status of `vex` when no statement names the image: nothing to attest.
NO_VEX_STATEMENTS = 3
IMAGE = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._/:-]*?)(?::[\w][\w.-]{0,127})?@sha256:(?P<digest>[0-9a-f]{64})$")
NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[0-9]+)?(?:/[a-z0-9._-]+)+$")


def main(argv):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    secrets = commands.add_parser("secrets")
    secrets.add_argument("findings")
    secrets.add_argument("predicate")
    secrets.add_argument("--image", required=True)
    secrets.add_argument("--version", required=True)
    secrets.add_argument("--started", required=True)
    secrets.add_argument("--finished", required=True)
    secrets.add_argument("--on-verified", choices=["fail", "record"], default="fail")
    malware = commands.add_parser("malware")
    malware.add_argument("log")
    malware.add_argument("predicate")
    malware.add_argument("--image", required=True)
    malware.add_argument("--version", required=True)
    malware.add_argument("--database", required=True)
    malware.add_argument("--root", default="/scan", help="Where the image's files were mounted for the scan")
    malware.add_argument("--started", required=True)
    malware.add_argument("--finished", required=True)
    malware.add_argument("--on-detection", choices=["fail", "record"], default="fail")
    vulnerabilities = commands.add_parser("vulnerabilities")
    vulnerabilities.add_argument("report")
    vulnerabilities.add_argument("predicate")
    vulnerabilities.add_argument("--scanner-uri", required=True)
    vulnerabilities.add_argument("--scanner-version", required=True)
    vulnerabilities.add_argument("--started", required=True)
    vulnerabilities.add_argument("--finished", required=True)
    vex = commands.add_parser("vex")
    vex.add_argument("document")
    vex.add_argument("predicate")
    vex.add_argument("--image", required=True)
    vex.add_argument("--subject-name", required=True)
    subjects = commands.add_parser("subjects")
    subjects.add_argument("image")
    subjects.add_argument("name")
    subjects.add_argument("output")
    args = parser.parse_args(argv)
    if args.command == "secrets":
        return secrets_predicate(args)
    if args.command == "malware":
        return malware_predicate(args)
    if args.command == "vulnerabilities":
        return vulnerability_predicate(args)
    if args.command == "vex":
        return vex_predicate(args)
    return write_subjects(args)


def secrets_predicate(args):
    counts = Counter()
    detectors = Counter()
    with open(args.findings, encoding="utf-8") as findings:
        for line in findings:
            line = line.strip()
            if not line.startswith("{"):
                continue  # trufflehog logs are not findings
            finding = json.loads(line)
            if "DetectorName" not in finding:
                continue
            result = finding_result(finding)
            counts[result] += 1
            detectors[(finding["DetectorName"], result)] += 1
    predicate = {
        "scanner": {"uri": TRUFFLEHOG_URI, "version": args.version},
        "image": args.image,
        "policy": {"failOn": "verified" if args.on_verified == "fail" else "nothing"},
        "result": {
            "verified": counts["verified"],
            "unknown": counts["unknown"],
            "unverified": counts["unverified"],
            "detectors": [
                {"name": name, "result": result, "count": count}
                for (name, result), count in sorted(detectors.items())
            ],
        },
        "metadata": {"scanStartedOn": args.started, "scanFinishedOn": args.finished},
    }
    write_json(args.predicate, predicate)
    summary = ", ".join(f"{counts[r]} {r}" for r in ("verified", "unknown", "unverified"))
    print(f"Secret scan of {args.image}: {summary}")
    if counts["verified"] and args.on_verified == "fail":
        names = sorted({name for name, result in detectors if result == "verified"})
        print(f"::error::{args.image} holds {counts['verified']} live secret(s) ({', '.join(names)}). "
              "Rotate them and rebuild; nothing was attested.")
        return 2
    return 0


def malware_predicate(args):
    detections, errors, summary = [], 0, {}
    with open(args.log, encoding="utf-8", errors="replace") as log:
        for line in log:
            line = line.rstrip("\n")
            if match := DETECTION.match(line):
                path = match["path"].removeprefix(args.root) or "/"
                detections.append({"path": path, "signature": match["signature"]})
            elif SCAN_ERROR.match(line):
                errors += 1
            elif ": " in line:
                key, value = line.split(": ", 1)
                summary[key.strip()] = value.strip()
    if "Scanned files" not in summary or "Infected files" not in summary:
        raise ValueError(f"{args.log} has no clamscan summary; the scan did not finish")
    infected = int(summary["Infected files"])
    if infected != len(detections):
        raise ValueError(f"clamscan reported {infected} infected file(s) but listed {len(detections)}")
    predicate = {
        "scanner": {"uri": CLAMAV_URI, "version": args.version,
                    "database": {"version": args.database, "signatures": int(summary.get("Known viruses", 0))}},
        "image": args.image,
        "policy": {"failOn": "detection" if args.on_detection == "fail" else "nothing"},
        "result": {
            "scannedFiles": int(summary["Scanned files"]),
            "scannedDirectories": int(summary.get("Scanned directories", 0)),
            "infected": infected,
            "errors": errors,
            "detections": sorted(detections, key=lambda d: (d["path"], d["signature"])),
        },
        "metadata": {"scanStartedOn": args.started, "scanFinishedOn": args.finished},
    }
    write_json(args.predicate, predicate)
    print(f"Malware scan of {args.image}: {predicate['result']['scannedFiles']} files, {infected} detection(s), "
          f"{errors} unreadable (ClamAV {args.version}, database {args.database})")
    if infected and args.on_detection == "fail":
        for detection in predicate["result"]["detections"]:
            print(f"::error::{args.image}: {detection['path']} matches {detection['signature']}")
        print("::error::Malware found in the image; nothing was attested.")
        return 2
    return 0


def vulnerability_predicate(args):
    with open(args.report, encoding="utf-8") as report:
        result = json.load(report)
    predicate = {
        "invocation": {
            "parameters": None,
            "uri": run_url(),
            "event_id": os.environ.get("GITHUB_RUN_ID", ""),
            "builder.id": os.environ.get("GITHUB_WORKFLOW_REF", ""),
        },
        "scanner": {
            "uri": args.scanner_uri,
            "version": args.scanner_version,
            "db": {"uri": "", "version": ""},
            "result": result,
        },
        "metadata": {"scanStartedOn": args.started, "scanFinishedOn": args.finished},
    }
    write_json(args.predicate, predicate)
    return 0


def vex_predicate(args):
    match = IMAGE.match(args.image)
    if not match:
        raise ValueError(f"image must be a reference pinned by digest (<name>@sha256:<digest>): {args.image}")
    if not NAME.match(args.subject_name):
        raise ValueError(f"subject name must be a repository without tag or digest: {args.subject_name}")
    vex = vex_module()
    try:
        document = vex.load(args.document)
        predicate = vex.rendered(document, f"{args.subject_name}@sha256:{match['digest']}")
    except vex.Invalid as error:
        raise ValueError(f"{args.document}: {error}") from None
    product_id = f"pkg:oci/{args.subject_name.rsplit('/', 1)[-1]}"
    if not predicate["statements"]:
        print(f"No VEX statement names {product_id} in {args.document}")
        return NO_VEX_STATEMENTS
    if run_url():
        predicate["@id"] = f"{run_url()}#{args.subject_name}@sha256:{match['digest']}"
    write_json(args.predicate, predicate)
    print(f"VEX for {args.subject_name}@sha256:{match['digest']}: {len(predicate['statements'])} statement(s)")
    return 0


def write_subjects(args):
    match = IMAGE.match(args.image)
    if not match:
        raise ValueError(f"image must be a reference pinned by digest (<name>@sha256:<digest>): {args.image}")
    if not NAME.match(args.name):
        raise ValueError(f"subject name must be a repository without tag or digest: {args.name}")
    with open(args.output, "w", encoding="utf-8") as output:
        output.write(f"{match['digest']}  {args.name}\n")
    return 0


def finding_result(finding):
    if finding.get("Verified"):
        return "verified"
    if finding.get("VerificationError"):
        return "unknown"
    return "unverified"


def run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else ""


def vex_module():
    """vex.py next to this file: python3 -I keeps the script's directory off sys.path."""
    spec = importlib.util.spec_from_file_location("vex", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vex.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.exit(f"::error::attest-release-image: {error}")
