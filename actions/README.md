# Shared CI actions

Pin callers to a reviewed **full commit SHA** in `archestra-ai/.github/actions/<name>@<sha>`.
Dependabot checks workflows and `actions/*` weekly, grouping Actions updates with a seven-day cooldown.
Checkout the caller's source first. All actions run inside the caller's job;
triggers, environments, permissions, service accounts and deployment checks stay there.

| Action | Required inputs | Optional inputs / outputs |
| --- | --- | --- |
| `google-auth` | `service_account`, `workload_identity_provider` | `setup_gcloud: 'true'`; `registry` also installs gcloud; `token_format: access_token` exposes `access_token`. Also outputs `project_id`, `credentials_file_path`. |
| `github-app-token` | `app-id`, `private-key` | `owner`, `repositories`, `permission-actions`, `permission-contents`, `permission-issues`, `permission-pull-requests`; outputs `token`. |
| `vercel-deploy` | `token`, `org-id`, `project-id` | `build-env`: JSON object of non-secret string values; outputs `deployment-url`. Always deploys production. |

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

Test locally with `python3 -m unittest discover -s actions/vercel-deploy -v`.
The Vercel tests use a fake CLI and never authenticate or deploy.
To roll back, revert a caller's pin or its migration PR; no credential rotation is required.
