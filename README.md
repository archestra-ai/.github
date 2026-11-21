# archestra-ai/.github

Organization-level GitHub workflows and default community health files for `archestra-ai`.

## Tests

`python3 tests/run_python_tests.py` runs every Python test in the repository,
one `unittest discover` process per directory, and prints a count per
directory. Pass directories to run only those, and unittest options after `--`
(`python3 tests/run_python_tests.py supply-chain-policy -- -v`). CI runs it in
the Shared Action Tests workflow with `RUN_NETWORK_TESTS=1`, which turns on the
staging-iap test that needs a Linux runner.

## Zizmor rollout

This repository contains the canonical `zizmor` workflow intended to be enforced with an organization repository ruleset using `Require workflows to pass before merging`.

The workflow checks out:

- the target repository under test
- this repository for the centrally managed `zizmor` config

This keeps the scan policy consistent across repositories and avoids per-repository suppressions or drift.

## TruffleHog secret scanning

[`trufflehog.yml`](.github/workflows/trufflehog.yml) scans the commits a pull request or merge group adds for credentials, and fails when one is verified live with its provider. An organization ruleset ("Require workflows to pass before merging", managed in `archestra-ai/infra` under `github/org/`) requires it on every repository, pinned to a commit of this file.

- It fetches only the new commits and their merge base with the base branch, not the whole history, and needs no secrets, so it runs on fork pull requests too.
- Unverified matches are not reported, and a scan that errors fails rather than passes.
- If it flags a credential, revoke it with the provider; removing it in a later commit does not help, since the commit stays fetchable on GitHub. A revoked credential no longer verifies, so a rerun passes.
- To change it, merge here, then move the ruleset's pin in `archestra-ai/infra`.

## Supply-chain policy

[`supply-chain-policy.yml`](.github/workflows/supply-chain-policy.yml) is enforced the same way: an organization ruleset in `archestra-ai/infra` requires it on every repository's pull requests and merge queue, from the same pinned tag. It checks that Dependabot updates use a seven-day cooldown, that pnpm waits seven days for new releases and skips install scripts, and that Cargo lockfiles are committed and used. It checks that every workflow is onboarded to the GitHub Actions workflow lockfile. It also verifies that every image a Dockerfile builds on is signed by its publisher, or is on the list of known unsigned images. See [supply-chain-policy/README.md](supply-chain-policy/README.md) for the rules, starter configuration for new repositories, and how to fix each finding.

## Image benchmark

[`image-benchmark.yml`](.github/workflows/image-benchmark.yml) checks a change's container images against the CIS Docker Benchmark's image checks with [Dockle](https://github.com/goodwithtech/dockle), before the change merges. An organization ruleset in `archestra-ai/infra` requires it on every repository's pull requests and merge queue. It has no `paths` filter, so it always runs and passes in seconds when no image changed. At release time, [`attest-release-image`](actions/attest-release-image) runs the same check ([`actions/image-benchmark`](actions/image-benchmark)) on the release digest and attests the result.

- **What it builds:** the images listed in the repository's `.github/images.json` whose Dockerfile, `.dockerignore`, list entry or waivers the change touches. It builds each one for `linux/amd64` into the runner's Docker daemon, without pushing or secrets, and fails on a FATAL or WARN check that no waiver in `.github/image-benchmark-waivers.json` covers.
- **Every Dockerfile is declared:** a Dockerfile the change adds or modifies must be in `images.json`, either under `images` with the key it is released as, or under `unreleased` with a reason. When `images.json` itself changes, every Dockerfile in the repository must be declared. A repository without Dockerfiles needs no list.

```json
{
  "images": [
    {"key": "docker.io/archestra/platform:slim", "dockerfile": "platform/Dockerfile", "context": "platform", "target": "unified-slim"}
  ],
  "unreleased": [{"dockerfile": "platform/dev/*/Dockerfile", "reason": "Local development images, never released."}]
}
```

- **Fields:** `key` is the image's key in the release request, which waivers match. `target`, `build-args`, `prepare`, `build-args-file` and `runner` are optional:
  - `prepare` is a command, run from the repository root, that generates a build context the repository doesn't commit. It can write `NAME=value` build arguments to the file `build-args-file` names.
  - `runner` is a GitHub-hosted Linux x64 label, such as `ubuntu-24.04-16core` for a large build.
- **Waivers:** `{"waivers": [{"images": ["<key or glob>"], "check": "CIS-DI-0001", "reason": "...", "alerts": ["<glob>"]}]}`. `alerts` is optional and narrows a waiver to the findings whose every alert matches one of its globs. The release attestation records each waiver and its reason.
- **Docker Hardened Images:** a Dockerfile that builds `FROM dhi.io/...` needs a login to pull. The workflow pulls those bases with the `DOCKERHUB_DHI_TOKEN` organization secret (a pull-only Docker Hub token), logs out, then builds from the local copies, so the build itself still runs without credentials. A repository with such an image needs access to that secret.
- **What it doesn't see:** the check reads the built image, but a change to other files in the build context doesn't trigger a build. The release check covers those.
- The planner and the check come from the same commit as the workflow file, so the ruleset's tag decides both. The planner's tests run with the other Python tests. The Shared Action Tests workflow also runs the plan, build and check end to end against a fixture repository.

## Shared Actions

- [CI authentication and deployment](actions/README.md): Google WIF, GitHub App
  tokens and Vercel source deployments.
- [Lint PR title](actions/README.md): the organization's Conventional Commits
  rules for pull request titles, in one config every repository shares.
- [Cargo Release Age](actions/cargo-release-age/README.md): enforces a seven-day
  minimum publication age for newly introduced crates.io versions. Consumers pin
  the action to a reviewed commit; the checker and behavior tests live here.

## Release Please

[`release-please.yml`](.github/workflows/release-please.yml) is a reusable workflow that runs Release Please for the calling repository. It always authenticates as a GitHub App and never uses the job's `GITHUB_TOKEN`, which the organization does not allow to create or approve pull requests.

- The App token is scoped to the calling repository with `contents: write` and `pull-requests: write` only. The App must be installed on that repository.
- Callers pass the App's ID and private key as the `APP_ID` and `APP_PRIVATE_KEY` secrets and can call it with `permissions: {}`.
- Keep the key in a deployment environment that only release branches can deploy to, and name it in the `environment` input. The calling job cannot set `environment` on a reusable workflow, so this workflow's job does; the caller still passes the secrets by name (`APP_ID: ${{ secrets.APP_ID }}`), and GitHub resolves them from that environment.
- The job creates releases for merged release PRs first, then creates or updates release PRs. `skip-github-release` and `skip-github-pull-request` turn either pass off, so a caller that needs its own steps in between calls the workflow twice.
- Outputs mirror `googleapis/release-please-action` for the root package (`release_created`, `tag_name`, `version`, ...) and for PRs (`prs_created`, `pr`, `prs`). Per-path outputs come from the `releases` JSON, for example `fromJSON(needs.release-please.outputs.releases)['platform--version']`.
- Keep cloud sign-in (for example Google Workload Identity) in the caller's jobs. A job inside this workflow presents `archestra-ai/.github/.github/workflows/release-please.yml` as its `job_workflow_ref`, not the caller's file.

Pin callers to a reviewed full commit SHA, as with the shared actions.

## Releases of this repository

This repository releases itself with Release Please ([`release.yml`](.github/workflows/release.yml), which calls `release-please.yml` from the same commit). Nobody tags by hand.

- Merges to `main` keep one `chore(main): release X.Y.Z` pull request up to date. It carries the next version and the changelog, computed from the conventional commit titles since the last release: `feat` bumps the minor, a breaking change (`!`) the major, anything else the patch.
- Merging it creates the `vX.Y.Z` tag on the merge commit, the GitHub release and the `CHANGELOG.md` entry. The tags keep the form callers pin (`@<full sha> # vX.Y.Z`) and Dependabot follows.
- The archestra-releaser App opens the PR and creates the release. Its ID and key (`APP_ID`, `APP_PRIVATE_KEY`) live in this repository's `releaser` environment, which only `main` can deploy to.
- The config and current version live in [`.github/release-please/`](.github/release-please/). Release Please started at `1.25.0` (`v1.25.0` = `7e43fad`), so the first release PR lists only the changes after it.

## Release publisher

[`release-publish.yml`](.github/workflows/release-publish.yml) is a reusable workflow that asks [release-publisher](https://github.com/archestra-ai/release-publisher) to publish a release run's attested staged images, charts and release assets, and waits for the publisher's run, the release's approval included (every release, prereleases too; dry runs need none). Callers wait here, so the 360-minute job limit is set once. Before the request, it attests what each image contains (SPDX and CycloneDX SBOMs, a secret scan, a ClamAV malware scan, a Dockle image benchmark (the CIS Docker Benchmark's image checks, with the project's `benchmark-waivers`), and with `vex-document` the image's OpenVEX statements, read from the release commit or from `vex-ref`; [`attest-release-image`](actions/attest-release-image)), and a live secret, malware, or a benchmark failure the project has not waived stops the release; [`attest-verification-summary`](actions/attest-verification-summary) signs a SLSA verification summary of each image's build provenance; the calling job grants `id-token: write`, `attestations: write` and `packages: read`.

- Attest build provenance with [`attest-provenance.yml`](#build-provenance) before this one, in the same run.
- Pass the staged digests as the `images` JSON. Charts and release assets come from artifacts of the calling run (`chart-artifact`, `asset-artifact`); `return-artifact` copies what the publisher returns (a Terraform provider's signed `release-assets`) into the calling run.
- Keep the release App's key in a deployment environment that only release refs can use, and name it in `environment`, as with Release Please.
- Steps after the publish (a GitHub release, notifications) stay in caller jobs that need this one.
- When nobody approves a release in time, the job fails after 360 minutes while the publisher run keeps waiting. Retry in the calling run once an approver is around: **Re-run failed jobs** re-runs this wait without the attest job (the publisher accepts an attestation from an earlier attempt of the same run), and the new request asks for a new approval. The publisher is idempotent. archestra re-runs all jobs instead (see the publisher's README, "Retry a failed publish").

The `release-images-publish` action does the request inside it; callers use this workflow.

Pin callers to a reviewed full commit SHA, as with the shared actions.

## Build provenance

[`attest-provenance.yml`](.github/workflows/attest-provenance.yml) is a reusable workflow that signs SLSA build provenance for what the calling run built: images by public name and digest (`<name>@sha256:<hex>`), files by `sha256sum` line. It signs in a job of its own that runs only its steps, so no build step can reach the signing identity and a build cannot forge its provenance. That, with GitHub-hosted runners, is what SLSA Build L3 asks for.

- Build in jobs without `id-token: write` or `attestations: write`, and pass the digests out as job outputs. The calling job grants this workflow `id-token: write` and `attestations: write`.
- The certificate names `archestra-ai/.github/.github/workflows/attest-provenance.yml` as the signer and the calling run as the build. Verify with `gh attestation verify ... --signer-workflow archestra-ai/.github/.github/workflows/attest-provenance.yml`.
- release-publisher accepts only this workflow's provenance for a project with `shared_provenance`. For other projects it is preferred, and provenance a project signs itself passes with a warning.
