# Previous Release Published

A merged release-please PR leaves a draft GitHub release that waits for its
approval in [release-publisher](https://github.com/archestra-ai/release-publisher).
This action fails the next release PR on the same line while that release is
still a draft, so releases publish in order. Every other PR passes at once.

- **Lines:** with `release-branches: 'false'`, every release is on one line.
  With `'true'`, the default branch releases prereleases (`X.Y.Z-rc.N`) and
  `release/X.Y` releases `X.Y.Z`.
- **Blocks:** a draft on the PR's line that is older than its version and
  newer than the line's newest published release. A draft deleted to abandon
  a version (tag kept) holds nothing back, nor does a draft older than a later
  published release.
- **Merge queue:** resolves the group's PRs (the `pr-N` in its ref and each
  squashed commit's `(#N)`) and applies the same rule.
- **Comment:** one comment per release PR, posted only when blocked and
  rewritten to "Unblocked" once clear.
- **Re-check:** the check runs on the PR's head commit, so `mode: recheck`
  re-runs a release PR's failed run once it is clear. Run it on a schedule,
  on `release: published` (a release published by hand), and by
  `workflow_dispatch` from the job that publishes releases: a publish with
  `GITHUB_TOKEN` starts no `release` event.
- **Retries:** a GitHub API call that fails with a 5xx, a 429, a rate-limit
  403, a dropped connection or a timeout is retried four times over about 45
  seconds, then fails the check. A refused request (401, 404, ...) is not
  retried.
- **Tokens:** only push access lists draft releases, so both modes need
  `contents: write`, though they write no contents. release-publisher is
  private, so the comment links its waiting runs, not the exact run.

## Caller

Every repository that requires the check uses the same file name, workflow
name and job name, so one organization ruleset can require
`Previous Release Published` everywhere. No checkout is needed.

```yaml
name: Previous Release Published

on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
  merge_group:
  release:
    types: [published]
  schedule:
    - cron: "4,19,34,49 * * * *"
  workflow_dispatch:

permissions: {}

jobs:
  check:
    name: Previous Release Published
    if: ${{ github.event_name == 'pull_request' || github.event_name == 'merge_group' }}
    runs-on: ubuntu-latest
    timeout-minutes: 5
    concurrency:
      group: previous-release-published-${{ github.event.pull_request.number || github.event.merge_group.head_sha }}
      cancel-in-progress: true
    permissions:
      contents: write # List draft releases (push access only).
      pull-requests: write # Read the PR and keep its one comment.
      actions: read # Link each draft's release run.
    steps:
      - uses: archestra-ai/.github/actions/previous-release-published@<full-reviewed-commit-sha>
        with:
          tag-prefix: v
          release-workflow: release.yml

  recheck:
    name: Re-check open release PRs
    if: ${{ github.event_name != 'pull_request' && github.event_name != 'merge_group' }}
    runs-on: ubuntu-latest
    timeout-minutes: 5
    concurrency:
      group: previous-release-published-recheck
      cancel-in-progress: false
    permissions:
      contents: write # List draft releases (push access only).
      pull-requests: read # List the open release PRs.
      actions: write # Re-run a release PR's failed check.
    steps:
      - uses: archestra-ai/.github/actions/previous-release-published@<full-reviewed-commit-sha>
        with:
          mode: recheck
          tag-prefix: v
          release-workflow: release.yml
```

A required check must report on every PR and merge group into the branches it
protects, so put the caller on each of them (the default branch and every
live `release/*`) before the ruleset requires it.

Test locally with
`python3 -m unittest discover -s actions/previous-release-published -v`.
