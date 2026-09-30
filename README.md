# archestra-ai/.github

Organization-level GitHub workflows and default community health files for `archestra-ai`.

## Zizmor rollout

This repository contains the canonical `zizmor` workflow intended to be enforced with an organization repository ruleset using `Require workflows to pass before merging`.

The workflow checks out:

- the target repository under test
- this repository for the centrally managed `zizmor` config

This keeps the scan policy consistent across repositories and avoids per-repository suppressions or drift.

## Shared Actions

- [Cargo Release Age](actions/cargo-release-age/README.md): enforces a seven-day
  minimum publication age for newly introduced crates.io versions. Consumers pin
  the action to a reviewed commit; the checker and behavior tests live here.

## Claude PR Reviews

[claude-coreteam-review.yml](.github/workflows/claude-coreteam-review.yml) routes reviews to the PR author's personal OAuth token. The shared worker preserves the `/review-pr` prompt, model, tools, and progress output. Drafts, WIP titles, and fork PRs are skipped. Missing tokens produce a notice and skip the review; another person's token is never substituted.

Each author adds an organization Actions secret named `CLAUDE_CODE_OAUTH_TOKEN_<GitHub user ID>`. Numeric IDs remain stable across username changes and work for usernames containing hyphens. The caller selects the PR author's secret and passes only that token. No user list or routing configuration is needed.

### Caller Workflow

Pin the reusable workflow to a published, reviewed commit. Event triggers stay in each caller repository.

```yaml
name: Claude Core Team Review
on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
permissions: {}
concurrency:
  group: claude-coreteam-review-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
jobs:
  review:
    permissions:
      contents: read
      pull-requests: write # Publish PR review comments.
      id-token: write # Authenticate through the Claude GitHub App.
    uses: archestra-ai/.github/.github/workflows/claude-coreteam-review.yml@COMMIT_SHA
    # This caller-side lookup passes only the selected token to the worker.
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets[format('CLAUDE_CODE_OAUTH_TOKEN_{0}', github.event.pull_request.user.id)] }} # zizmor: ignore[overprovisioned-secrets,obfuscation]
```

Pass these optional `with` inputs on the caller job:

| Input | Default | Purpose |
| --- | --- | --- |
| `setup_repository` | `false` | Run the caller-owned `.github/actions/setup-claude-review` action after checkout. |
| `use_github_token` | `false` | Use `github-actions[bot]` when the Claude GitHub App is unavailable. |
| `use_sticky_comment` | `true` | Update a sticky review comment. |

Archestra sets `setup_repository: true`. OpenAPPA sets `use_github_token: true` and `use_sticky_comment: false`. Keep the caller and worker concurrency groups distinct; matching groups can cancel the caller.

### Repository Setup

Callers own their dependency installation and environment configuration. To enable setup, set `setup_repository: true` and provide a composite action at `.github/actions/setup-claude-review/action.yml` in the caller repository. The shared worker runs it after checkout, before Claude, in the same job. A separate caller job cannot prepare the worker's filesystem or installed tools.

Archestra's action delegates to its existing `.github/actions/setup-env`. Other repositories can provide their own action or leave setup disabled. With setup disabled, the caller needs no setup action. The shared workflow makes no assumptions about package managers, language versions, or repository layout.

### Organization Secret Setup

Generate a long-lived token with `claude setup-token`. With `gh` signed into your own GitHub account, store the token using the CLI's interactive prompt:

```bash
gh secret set "CLAUDE_CODE_OAUTH_TOKEN_$(gh api user --jq .id)" --org archestra-ai --visibility all
```

`--visibility all` makes these secrets available to current and future organization repositories, including public repositories. Repository secrets with matching names take precedence. Repositories using Claude GitHub App authentication need the app installed.

### Maintaining The Shared Implementation

The Claude action version and default model live in `actions/run-claude`. When the action changes, publish its commit and update the reusable workflow's action pin. Update consumer pins when publishing a new reusable workflow revision.

Validate workflow syntax with actionlint and security policy with zizmor. Live authentication and comment publication require a GitHub Actions run in each caller repository.
