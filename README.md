# archestra-ai/.github

Organization-level GitHub workflows and default community health files for `archestra-ai`.

## Zizmor rollout

This repository contains the canonical `zizmor` workflow intended to be enforced with an organization repository ruleset using `Require workflows to pass before merging`.

The workflow checks out:

- the target repository under test
- this repository for the centrally managed `zizmor` config

This keeps the scan policy consistent across repositories and avoids per-repository suppressions or drift.

## Shared Actions

- [CI authentication and deployment](actions/README.md): Google WIF, GitHub App
  tokens and Vercel source deployments.
- [Cargo Release Age](actions/cargo-release-age/README.md): enforces a seven-day
  minimum publication age for newly introduced crates.io versions. Consumers pin
  the action to a reviewed commit; the checker and behavior tests live here.

## Archestra Runtime Reviews

[archestra-ai/code-review-action](https://github.com/archestra-ai/code-review-action) reviews a pull request on an Archestra Agent Runtime and posts the review as the Archestra Lobster App, with inline findings. [`archestra-runtime-review.yml`](.github/workflows/archestra-runtime-review.yml) runs it on every pull request of every repository in the organization: an organization ruleset with "Require workflows to pass before merging" names this file on `main`, as the zizmor rollout does. No repository holds a workflow of its own, and a change merged here is live everywhere at once.

What it does, and what it needs:

- It reviews a pull request only when a maintainer (admin or maintain role on the repository) opened it and started the run. Every other pull request runs nothing and says nothing. A blocking finding requests changes; a later clean review dismisses it and says so. Ineligible pull requests still skip the review. For eligible pull requests, tunnel, authentication, and runtime errors fail the required check; completed reviews use change requests for blocking findings.
- The Archestra Lobster App must be installed on the repositories. Each maintainer connects GitHub once in Archestra (their name, **Credentials**, `lobster-github`) and has use on the review gateway and its agents.
- The review gateway accepts the job's GitHub Actions identity, and Archestra's VPN-only staging is reached through a short-lived IAP tunnel. Both trust exactly `archestra-ai/.github/.github/workflows/archestra-runtime-review.yml@refs/heads/main`; the tunnel trust lives in `archestra-ai/infra` (`gcp/workload_identity`). Neither changes when this file changes.
- The `ARCHESTRA_REVIEW_GATEWAY_URL` and `ARCHESTRA_REVIEW_AGENTS` variables of a repository override the staging gateway and agents written in the file.
- A ruleset delivers only opened, synchronize and reopened: comment commands, team review requests and ready-for-review events do not reach it.

To change the review, change this file and merge. The only pins that move are the commits of the actions it uses.
