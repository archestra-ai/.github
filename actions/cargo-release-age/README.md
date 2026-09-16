# Cargo Release Age

Reject new crates.io versions younger than seven days, including transitive dependencies.
The action compares the checked-out lockfiles with a trusted base commit. It fetches
that commit from the consumer's `origin`, then runs the bundled Python checker.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  with:
    persist-credentials: false
- uses: archestra-ai/.github/actions/cargo-release-age@<full-reviewed-commit-sha>
  with:
    base-sha: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}
    lockfiles: |
      ai-labs/Cargo.lock
```

Pin the action to a reviewed full commit SHA. Consumers own their workflow triggers,
base commit selection, and required-check dependencies. For `push`, use
`github.event.before`. An absent or invalid base SHA fails the check.

The consumer must be checked out at the workspace root. The runner needs Bash,
Git, Python 3.11 or newer, and network access to `origin` and crates.io. Private
repositories must configure authentication for fetching their base commit.
No Cargo toolchain or Python packages are required.

Registry failures, missing publication metadata, unsupported registries, and missing
base lockfiles fail closed. Existing base versions are grandfathered; local and git
dependencies are outside the registry-age check. Rerun CI once a blocked version
reaches seven days old. The action has no age-policy bypass input.

Stable Cargo installs are not intercepted. Cargo's native
[minimum publish age](https://doc.rust-lang.org/cargo/reference/unstable.html#min-publish-age)
is currently nightly-only. This action keeps consumers on their existing toolchain.

## Local Use

Check out this repository at the same commit pinned in the consuming workflow.
From the consumer repository, run:

```sh
python3 /path/to/org-github/actions/cargo-release-age/check-cargo-release-age.py \
  --base-ref origin/main --lockfile ai-labs/Cargo.lock
```

Fetch the target branch first. Repeat `--lockfile` for each lockfile. Run the policy
tests from this repository with:

```sh
python3 -m unittest discover -s actions/cargo-release-age -p 'test_*.py'
```
