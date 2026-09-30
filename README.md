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

Core maintainers can enable reviews for their PRs by running `claude setup-token`, then pasting the token into this command's prompt while signed into their own GitHub account:

```bash
gh secret set "CLAUDE_CODE_OAUTH_TOKEN_$(gh api user --jq '.login | ascii_upcase | gsub("-"; "_")')" --org archestra-ai --visibility all
```
