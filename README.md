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

## Claude PR Reviews

Core maintainers can enable reviews for their PRs by running `claude setup-token`, then pasting the token into this command's prompt while signed into their own GitHub account:

```bash
gh secret set "CLAUDE_CODE_OAUTH_TOKEN_$(gh api user --jq '.login | ascii_upcase | gsub("-"; "_")')" --org archestra-ai --visibility selected --repos .github,archestra,OpenAPPA
```

## Archestra Runtime Reviews

[archestra-ai/code-review-action](https://github.com/archestra-ai/code-review-action) reviews a pull request on an Archestra Agent Runtime and posts the review as a comment with inline findings. Each run acts as the person who caused the event, with that person's Archestra platform token: their credentials, their Claude account, their costs. The reusable workflow [`archestra-runtime-review.yml`](.github/workflows/archestra-runtime-review.yml) runs it; a repository calls it from a short workflow of its own.

To enable it for a repository:

1. Set the variables `ARCHESTRA_REVIEW_GATEWAY_URL` (the MCP gateway endpoint) and `ARCHESTRA_REVIEW_AGENTS` (agent IDs, one per line) on the repository, or as organization variables shared with it.
2. Each person stores their Archestra platform token (Personal Settings in Archestra) as the secret `ARCHESTRA_TOKEN_<LOGIN>` (the GitHub login in upper case, `-` as `_`), as for Claude reviews. Organization owners share it with the repositories:

   ```bash
   gh secret set "ARCHESTRA_TOKEN_$(gh api user --jq '.login | ascii_upcase | gsub("-"; "_")')" --org archestra-ai --visibility selected --repos archestra,OpenAPPA
   ```

   A repository admin sets it on one repository with `--repo archestra-ai/<repo>` instead. Without a token for the author, the job skips with a notice.
3. Add the caller workflow. `archestra-ai/archestra/.github/workflows/archestra-runtime-review.yml` is the reference: a job resolves the name of the author's secret with the action's `resolve-actor-token.yml`, and the review job calls this workflow with that one secret, so no job gets the whole secrets context.

Fix commands in the review comment (`/archestra fix`, `/archestra fix-ci`) need a GitHub App that can push (`ARCHESTRA_REVIEW_APP_ID` and `ARCHESTRA_REVIEW_APP_PRIVATE_KEY`), comment triggers in the caller, and `fix-commands: true`. A private repository needs that App, or `clone-with-job-token: true`, for the runtime to clone it.

To run the review on every pull request of the organization without a caller in each repository, an organization owner adds a workflow of this repository to a ruleset with "Require workflows to pass before merging", as the zizmor rollout does. The review then becomes a required check: the job passes when it skips, and comment commands cannot run through a ruleset.
