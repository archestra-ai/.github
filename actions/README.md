# Shared CI actions

Pin callers to a reviewed **full commit SHA** in `archestra-ai/.github/actions/<name>@<sha>`.
Dependabot checks workflows and `actions/*` weekly, grouping Actions updates with a seven-day cooldown.
Checkout the caller's source first (`lint-pr-title` needs none). All actions run inside the caller's job;
triggers, environments, permissions, service accounts and deployment checks stay there.

| Action | Required inputs | Optional inputs / outputs |
| --- | --- | --- |
| `google-auth` | `service_account`, `workload_identity_provider` | `setup_gcloud: 'true'`; `registry` also installs gcloud; `token_format: access_token` exposes `access_token`. Also outputs `project_id`, `credentials_file_path`. |
| `github-app-token` | `app-id`, `private-key` | `owner`, `repositories`, `permission-actions`, `permission-contents`, `permission-issues`, `permission-pull-requests`; outputs `token`. |
| `vercel-deploy` | `token`, `org-id`, `project-id` | `build-env`: JSON object of non-secret string values; outputs `deployment-url`. Always deploys production. |
| `release-images-publish` | `token`, `project`, `version`, `images-file` | `latest` (space-separated channels), `charts` (newline-separated `<chart>-<version>.tgz` files), `assets` (newline-separated release asset files, for a Terraform provider), `smoke`, `gate`, `app-id` and `private-key` (mint a new publisher token every 45 minutes, so the wait outlives the one-hour token), `publisher-repository`; outputs `run-url` and `run-id`. Sends a [release-publisher](https://github.com/archestra-ai/release-publisher) request for this run attempt, polls the publisher run (retrying failed polls) and fails when it fails. Attest every image and chart in the same job first. Release callers use the [`release-publish.yml`](../.github/workflows/release-publish.yml) reusable workflow, which runs this action and waits up to 360 minutes. `wait: 'false'` hands the request off instead (the step ends once the publisher run exists) and `request-file` keeps the request for `release-publish-verify`. |
| `release-publish-verify` | `token`, `attestation-token`, `request-file`, `requester`, `signer-repository`, `signer-workflow` | `image-tag` (`{version}` or `v{version}`), `chart-repositories` (`<chart> oci://<repository>` lines), `publisher-repository`; outputs `run-url`, `version`. Run first in the workflow that finishes a handed-off release: the request's publisher run must be `publish.yml` on `main`, dispatched by `requester`, and successful; every public `<name>:<tag>` must hold the attested digest with a provenance attestation from `signer-workflow`; every chart must pull with the attested sha256. Needs `docker`, `gh` and `helm` (hosted Ubuntu runners have them). |
| `lint-pr-title` | none | `extra-types`: commit types the repository accepts on top of the organization's list (commas, spaces or newlines). Lints the pull request title against the organization's Conventional Commits rules in [`commitlint.config.js`](lint-pr-title/commitlint.config.js); on `merge_group` it passes without linting, so a required check still posts. Needs no checkout and no job permissions. Keep the job name `PR Title Linter` where a ruleset requires that check. |
| `staging-iap` | `gateway-url` on start | Internal Archestra PR reviews only. Opens the existing IAP staging HTTPS tunnel; call again with `operation: stop` in an `always()` step. Requires `id-token: write`. |

Google callers need job permission `id-token: write`. Authentication uses WIF only;
temporary credential cleanup remains enabled. There are no service-account key inputs.
Composite actions preserve the caller's workflow identity, including existing GCP
`job_workflow_ref` trust conditions. [GitHub OIDC reference](https://docs.github.com/actions/deployment/security-hardening-your-deployments/using-openid-connect-with-reusable-workflows).

App tokens retain upstream masking and end-of-job revocation. Preserve each caller's
repository and permission inputs when migrating. An explicit `owner` without
`repositories` grants installation-wide repository scope; avoid that combination.
Omitted permissions retain upstream installation defaults.

Vercel uses CLI `59.15.1`, uploads source and builds remotely. It supports team-scoped
tokens without `vercel pull` or `--prebuilt`. Keep project-specific verification and
smoke tests in the caller. Hosted Ubuntu runners supply Python 3 and Node/npm.

Test locally with `python3 -m unittest discover -s actions/vercel-deploy -v`
`python3 -m unittest discover -s actions/release-images-publish -v`
and `python3 -m unittest discover -s actions/release-publish-verify -v`.
The Vercel tests use a fake CLI and never authenticate or deploy.
To roll back, revert a caller's pin or its migration PR; no credential rotation is required.
