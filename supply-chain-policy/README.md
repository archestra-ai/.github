# Supply-chain policy

[`supply-chain-policy.yml`](../.github/workflows/supply-chain-policy.yml) checks
that a repository delays new dependency releases and does not run dependency
install scripts. An organization ruleset ("Require workflows to pass before
merging") runs it on every pull request and merge queue entry, as with zizmor.

Each rule applies only to the ecosystems a repository uses. A **failure** blocks
the merge. A **warning** shows as an annotation and in the job summary and does
not block.

| Rule | Level |
| --- | --- |
| `.github/dependabot.yml` exists when the repository has GitHub Actions workflows or npm, pnpm, Bun, Cargo or Go dependencies | fail |
| Every `updates` entry sets `cooldown.default-days` of 7 or more; any `semver-*-days` is 7 or more; `cooldown.include` is not used to narrow it | fail |
| An `updates` entry exists for `github-actions`, `npm`, `bun`, `cargo` and `gomod` when used | fail |
| An `updates` entry exists for Python (`uv` or `pip`), Docker, Compose, Terraform, Helm, dev containers and the like when used | warn |
| Lockfile, Go module and composite action directories are under an entry's `directory`/`directories` | warn |
| Every pnpm workspace sets `minimumReleaseAge` of 10080 or more and `ignoreScripts: true` in `pnpm-workspace.yaml` | fail |
| `packageManager` does not pin a pnpm older than 10.16.0, which ignores `minimumReleaseAge` | fail |
| Every Cargo workspace (and package outside one) commits `Cargo.lock` | fail |
| `cargo install` in workflows and composite actions uses `--locked` | fail |
| `cargo build`, `test`, `clippy`, `run`, `check` and the like use `--locked` (or `--frozen`) | warn |
| npm projects set `min-release-age=7` and `ignore-scripts=true` in `.npmrc`; Yarn sets `npmMinimalAgeGate: 7d`; Bun sets `install.minimumReleaseAge` | warn |
| uv projects set a relative `exclude-newer` of 7 days or more | warn |

Paths under `node_modules`, `vendor`, `third_party`, `testdata`, `fixtures` and
`test-fixtures` directories are ignored.

### Why some rules only warn

- **Python, Docker, Terraform, Helm coverage.** These are mostly tooling,
  benchmarks and base images across the organization, and many repositories
  update them by hand on purpose. A missing entry is a staleness gap, not a way
  for a fresh release to slip in.
- **npm, Yarn and Bun age gates.** The organization standardizes on pnpm.
  npm's `min-release-age` exists only in recent npm 11 releases, and the npm
  bundled with older Node versions ignores it silently, so failing on it would
  promise a protection CI may not have.
- **uv `exclude-newer`.** Relative durations are recent; an older uv in CI
  rejects the setting and breaks the build.
- **`--locked` outside `cargo install`.** `Cargo.lock` is required and already
  pins every version; `--locked` only turns a stale lockfile into an error.
  `cargo install` ignores the lockfile without it and resolves the newest
  matching versions, so that one fails.

## Cooldowns do not delay security fixes

Dependabot's `cooldown` applies only to version updates: "The `cooldown` option
is only available for *version* updates, not *security* updates"
([options reference](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference#cooldown-)).
Security update pull requests open as soon as an advisory is published.

pnpm's `minimumReleaseAge` does apply to every install, including a security
fix. When a fix cannot wait seven days, add the exact version to
`minimumReleaseAgeExclude` with a comment naming the advisory and the date to
remove it.

## Starter configuration

### `.github/dependabot.yml`

Keep the entries for the ecosystems the repository uses. `directories` accepts
globs.

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directories:
      - /
      - /.github/actions/*
    schedule:
      interval: weekly
    groups:
      github-actions-dependencies:
        patterns:
          - "*"
    labels:
      - dependencies
    commit-message:
      prefix: deps
    cooldown:
      default-days: 7

  - package-ecosystem: npm # pnpm and Yarn too; Bun uses `bun`
    directory: /
    schedule:
      interval: weekly
    groups:
      npm-dependencies:
        patterns:
          - "*"
        update-types:
          - minor
          - patch
    labels:
      - dependencies
    commit-message:
      prefix: deps
    cooldown:
      default-days: 7

  - package-ecosystem: cargo # or gomod, uv, docker, terraform, ...
    directory: /
    schedule:
      interval: weekly
    labels:
      - dependencies
    commit-message:
      prefix: deps
    cooldown:
      default-days: 7
```

### `pnpm-workspace.yaml`

A single-package repository needs the file too; it needs no `packages` list.

```yaml
# Do not run dependency lifecycle scripts. Run `pnpm rebuild <package>` after
# review when a package needs its build step.
ignoreScripts: true

# Install only versions published at least seven days ago (minutes).
minimumReleaseAge: 10080
# minimumReleaseAgeExclude:
#   # <advisory>: urgent fix published <date>; remove after <date + 7 days>.
#   - package@1.2.3
```

`ignoreScripts` also skips the project's own `preinstall`, `postinstall` and
`prepare` scripts. Run them explicitly where they are needed, for example
`pnpm run postinstall` after `pnpm install` in CI.

## New repositories

Start every new repository with the starter `dependabot.yml` above (keep the
`github-actions` entry even before the first workflow lands) and, for
JavaScript, the `pnpm-workspace.yaml` settings. The check then passes from the
first pull request.

## Running locally

Needs Python 3.11 or newer and [mikefarah yq](https://github.com/mikefarah/yq)
v4 on `PATH` (preinstalled on GitHub-hosted Ubuntu runners). From a checkout of
this repository:

```sh
python3 supply-chain-policy/check_supply_chain_policy.py /path/to/repository
python3 -m unittest discover -s supply-chain-policy -p 'test_*.py'
```

`--json` prints the findings as JSON. The exit status is 1 when any rule fails.
Only files tracked by Git are checked when the path is the root of a Git
checkout.
