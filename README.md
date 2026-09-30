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

[claude-coreteam-review.yml](.github/workflows/claude-coreteam-review.yml) routes reviews to the PR author's personal OAuth token. The shared worker preserves the `/review-pr` prompt, model, tools, and progress output. Unknown authors, drafts, WIP titles, and fork PRs are skipped. Missing tokens produce a notice and skip the review; another person's token is never substituted.

| GitHub author | Organization Actions secret |
| --- | --- |
| `iskhakov` | `ILDAR_CLAUDE_CODE_OAUTH_TOKEN` |
| `joeyorlando` | `JOEY_CLAUDE_CODE_OAUTH_TOKEN` |
| `piercypixel` | `MARK_CLAUDE_CODE_OAUTH_TOKEN` |

Each caller needs access to these organization secrets. Secrets stored only in this repository are unavailable to callers. The workflow passes only the selected author's secret to the review worker.

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
    secrets:
      ILDAR_CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.ILDAR_CLAUDE_CODE_OAUTH_TOKEN }}
      JOEY_CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.JOEY_CLAUDE_CODE_OAUTH_TOKEN }}
      MARK_CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.MARK_CLAUDE_CODE_OAUTH_TOKEN }}
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

GitHub cannot return existing repository secret values. Each token owner must supply their token again through organization Actions settings or the CLI's interactive prompt. Do not put token values in workflow files, command arguments, or PR descriptions.

```bash
gh secret set ILDAR_CLAUDE_CODE_OAUTH_TOKEN --org archestra-ai --visibility selected --repos archestra,OpenAPPA
gh secret set JOEY_CLAUDE_CODE_OAUTH_TOKEN --org archestra-ai --visibility selected --repos archestra,OpenAPPA
gh secret set MARK_CLAUDE_CODE_OAUTH_TOKEN --org archestra-ai --visibility selected --repos archestra,OpenAPPA
```

Publish the shared actions and workflows before merging caller changes. Preserve the action commits referenced by the workflows when merging; avoid squashing away pinned commits. Configure organization secrets for both caller repositories. Then merge the callers and verify a review in each repository. The Claude GitHub App must remain installed in repositories using its authentication mode.

Repository secrets with matching names override organization secrets. Remove those repository copies after organization setup to use the shared values. Verify a subsequent review after removing them. Keep organization access limited to intended callers; add more repositories when they adopt the workflow. Existing Archestra mention workflows resolve the same secret names and can use these organization secrets too.

### Maintaining The Shared Implementation

Author routing and its behavior tests live in `actions/claude-review-context`. The Claude action version and default model live in `actions/run-claude`. When either action changes, publish its commit and update the reusable workflow's action pins. Update consumer pins when publishing a new reusable workflow revision.

Run routing tests with `node --test actions/claude-review-context/select-token.test.cjs`. Validate workflow syntax with actionlint and security policy with zizmor. Live authentication and comment publication require a GitHub Actions run in each caller repository.
