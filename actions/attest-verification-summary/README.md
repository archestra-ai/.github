# attest-verification-summary

Signs a [SLSA verification summary](https://slsa.dev/spec/v1.1/verification_summary) (VSA) for a release image: a
statement that its build provenance was checked against the policy below, and passed. A consumer can verify this one
attestation, signed by the shared release workflow, instead of checking the provenance policy itself.

The release workflow ([`release-publish.yml`](../../.github/workflows/release-publish.yml)) runs it for every image,
next to [`attest-release-image`](../attest-release-image). release-publisher verifies the summary like the other
content attestations and attaches it to the published image.

## Policy

An image passes, and its summary claims `SLSA_BUILD_LEVEL_3`, when it has at least one build provenance attestation
(`https://slsa.dev/provenance/v1`) for its digest that:

- was signed by the shared [`attest-provenance.yml`](../../.github/workflows/attest-provenance.yml) workflow, which
  signs in a job of its own that no build step runs in, so a build cannot forge its provenance;
- names the project's repository as its source;
- was built on a GitHub-hosted runner (`--deny-self-hosted-runners`); and
- is recorded in the public Sigstore transparency log.

`gh attestation verify` checks the signature and the first three; the action checks all four again before it signs.
The summary lists each provenance it relied on (`inputAttestations`) by the run that signed it and the SHA-256 of its
bundle.

An image with no provenance that meets the policy (a project still signing provenance in its own workflow) gets a
warning and no summary.

## Verify

```sh
gh attestation verify oci://docker.io/archestra/platform:<version> \
  --owner archestra-ai \
  --signer-workflow archestra-ai/.github/.github/workflows/release-publish.yml \
  --predicate-type https://slsa.dev/verification_summary/v1
```
