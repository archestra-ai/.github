# archestra-ai/.github

Organization-level GitHub workflows and default community health files for `archestra-ai`.

## Zizmor rollout

This repository contains the canonical `zizmor` workflow intended to be enforced with an organization repository ruleset using `Require workflows to pass before merging`.

The workflow checks out:

- the target repository under test
- this repository for the centrally managed `zizmor` config

This keeps the scan policy consistent across repositories and avoids per-repository suppressions or drift.
