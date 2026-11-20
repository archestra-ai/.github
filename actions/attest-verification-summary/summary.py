#!/usr/bin/env python3
"""Builds the SLSA verification summary attest-verification-summary signs.

  summary.py <verify.json> <predicate.json> --image REF --subject-name NAME --repository OWNER/REPO
             --signer-workflow PATH --time T

<verify.json> is `gh attestation verify --format json` for the image's build
provenance under the policy in README.md. Every attestation it returns must
meet that policy here too (repository, signer workflow, GitHub-hosted runner,
public transparency log), or nothing is written and the exit status is 1.
The predicate (https://slsa.dev/verification_summary/v1) names those
attestations by their bundle's SHA-256 and claims SLSA Build Level 3.
"""

import argparse
import hashlib
import json
import re
import sys

VSA_TYPE = "https://slsa.dev/verification_summary/v1"
PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
VERIFIER = "https://github.com/archestra-ai/.github/tree/main/actions/attest-verification-summary"
POLICY = "https://github.com/archestra-ai/.github/blob/main/actions/attest-verification-summary/README.md#policy"
IMAGE = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._/:-]*?)(?::[\w][\w.-]{0,127})?@sha256:(?P<digest>[0-9a-f]{64})$")
NAME = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[0-9]+)?(?:/[a-z0-9._-]+)+$")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("verified")
    parser.add_argument("predicate")
    parser.add_argument("--image", required=True)
    parser.add_argument("--subject-name", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--signer-workflow", required=True)
    parser.add_argument("--time", required=True)
    args = parser.parse_args(argv)
    match = IMAGE.match(args.image)
    if not match:
        raise ValueError(f"image must be a reference pinned by digest (<name>@sha256:<digest>): {args.image}")
    if not NAME.match(args.subject_name):
        raise ValueError(f"subject name must be a repository without tag or digest: {args.subject_name}")
    with open(args.verified, encoding="utf-8") as verified:
        results = json.load(verified)
    if not isinstance(results, list) or not results:
        raise ValueError("gh attestation verify returned no attestation")
    inputs = [checked(result, match["digest"], args) for result in results]
    digest = match["digest"]
    short = args.subject_name.rsplit("/", 1)[-1]
    predicate = {
        "verifier": {"id": VERIFIER},
        "timeVerified": args.time,
        "resourceUri": f"pkg:oci/{short}@sha256%3A{digest}?repository_url={args.subject_name}",
        "policy": {"uri": POLICY},
        "inputAttestations": sorted(inputs, key=lambda item: item["uri"]),
        "verificationResult": "PASSED",
        "verifiedLevels": ["SLSA_BUILD_LEVEL_3"],
        "slsaVersion": "1.1",
    }
    with open(args.predicate, "w", encoding="utf-8") as output:
        json.dump(predicate, output, indent=2, sort_keys=True)
        output.write("\n")
    print(f"Verified the build provenance of {args.subject_name}@sha256:{digest}: "
          f"{len(inputs)} attestation(s) from {args.signer_workflow}, SLSA Build Level 3")
    return 0


def checked(result, digest, args):
    """The input attestation entry for one verified provenance, after checking
    it against the policy again: gh verified it, and this is what is claimed."""
    statement = result["verificationResult"]["statement"]
    certificate = result["verificationResult"]["signature"]["certificate"]
    bundle = result["attestation"]["bundle"]
    if statement.get("predicateType") != PROVENANCE_TYPE:
        raise ValueError(f"not build provenance: {statement.get('predicateType')}")
    if not any(subject.get("digest", {}).get("sha256") == digest for subject in statement.get("subject", [])):
        raise ValueError(f"the provenance does not name sha256:{digest}")
    source = certificate.get("sourceRepositoryURI", "")
    if source != f"https://github.com/{args.repository}":
        raise ValueError(f"provenance from {source}, not {args.repository}")
    signer = certificate.get("buildSignerURI", "")
    if not signer.startswith(f"https://github.com/{args.signer_workflow}@"):
        raise ValueError(f"provenance signed by {signer}, not {args.signer_workflow}")
    if certificate.get("runnerEnvironment") != "github-hosted":
        raise ValueError(f"provenance built on a {certificate.get('runnerEnvironment')} runner")
    if not (bundle.get("verificationMaterial") or {}).get("tlogEntries"):
        raise ValueError("the provenance has no public transparency log entry")
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    return {"uri": certificate.get("runInvocationURI", ""), "digest": {"sha256": hashlib.sha256(canonical).hexdigest()}}


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        sys.exit(f"::error::attest-verification-summary: {error}")
