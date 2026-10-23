# Shared CI secrets

**No secret values or GitHub settings change in these PRs.** Callers keep their
current names, so org secrets can replace identical repository secrets later.
Repository secrets override org secrets: verify each migration before deleting a copy.

| Candidate org secrets | Selected repositories | Condition |
| --- | --- | --- |
| `ARCHESTRA_RELEASER_GITHUB_APP_ID`, `ARCHESTRA_RELEASER_GITHUB_APP_PRIVATE_KEY` | `archestra`, `website`, `OpenAPPA`, `logs-analyser`, `terraform-provider-archestra` | Confirm these represent the same installed App; names alone do not prove values match. |
| `CLAUDE_CODE_OAUTH_TOKEN_<USERNAME>` | Only repos where that person enables reviews, currently `.github` / `archestra` / `OpenAPPA` | The token owner supplies their own value; grant no blanket org access. |

Use **selected repositories**, never `all`. GitHub cannot return existing secret
values; an authorized owner must supply them at migration time. Explicit action
inputs continue to select the right credential for each caller.

Keep these separate:

- Vercel tokens and project IDs: distinct projects; keep tokens scoped per project/team.
  Team/project IDs can become variables after checking their values.
- Google service accounts: keep separate deploy/publish identities. WIF provider IDs
  are non-secret and can become variables, but must still match each account's trust.
- Website dispatch, release-freeze and OpenAPPA updater Apps: different permissions.
- OpenRouter/Turbo tokens: matching names do not prove shared ownership or quotas.

For an approved migration: add the selected org secret, validate one repository at a
time using its normal trusted workflow, then remove its repository copy. Restore
that copy to roll back. Never put credential values in a PR, workflow output or logs.
