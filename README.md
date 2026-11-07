# archestra-ai/.github

Organization-level GitHub workflows and default community health files for `archestra-ai`.

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

## Shared Actions

- [CI authentication and deployment](actions/README.md): Google WIF, GitHub App
  tokens and Vercel source deployments.
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

## Archestra Runtime Reviews

[archestra-ai/code-review-action](https://github.com/archestra-ai/code-review-action) reviews a pull request on an Archestra Agent Runtime and posts the review as the Archestra Lobster App, with inline findings. [`archestra-runtime-review.yml`](.github/workflows/archestra-runtime-review.yml) runs it on every pull request of every repository in the organization: an organization ruleset with "Require workflows to pass before merging" names this file on `main`, as the zizmor rollout does. No repository holds a workflow of its own, and a change merged here is live everywhere at once.

What it does, and what it needs:

- It reviews a pull request only when a maintainer (admin or maintain role on the repository) opened it and started the run. Every other pull request runs nothing and says nothing. A blocking finding requests changes; a later clean review dismisses it and says so. Ineligible pull requests still skip the review. For eligible pull requests, tunnel, authentication, and runtime errors fail the required check; completed reviews use change requests for blocking findings.
- The Archestra Lobster App must be installed on the repositories. Each maintainer connects GitHub once in Archestra (their name, **Credentials**, `lobster-github`) and has use on the review gateway and its agents.
- The review gateway accepts the job's GitHub Actions identity, and Archestra's VPN-only staging is reached through a short-lived IAP tunnel. Both trust exactly `archestra-ai/.github/.github/workflows/archestra-runtime-review.yml@refs/heads/main`; the tunnel trust lives in `archestra-ai/infra` (`gcp/workload_identity`). Neither changes when this file changes.
- The `ARCHESTRA_REVIEW_GATEWAY_URL` repository variable (falling back to the `ARCHESTRA_CODE_REVIEW_GATEWAY_URL` secret) configures the staging gateway URL.
- A ruleset delivers only opened, synchronize and reopened: comment commands, team review requests and ready-for-review events do not reach it.

To change the review, change this file and merge. The only pins that move are the commits of the actions it uses.
