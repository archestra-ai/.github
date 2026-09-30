const { appendFileSync, readFileSync } = require("node:fs");

function selectToken({ event, eventName, repository, repositoryOwner }) {
  const pr = event.pull_request;
  if (
    eventName !== "pull_request" ||
    repositoryOwner !== "archestra-ai" ||
    !["opened", "synchronize", "ready_for_review", "reopened"].includes(event.action) ||
    !pr ||
    pr.base?.repo?.full_name !== repository ||
    pr.head?.repo?.full_name !== repository ||
    pr.draft !== false ||
    typeof pr.title !== "string" ||
    pr.title.toLowerCase().includes("[wip]")
  ) {
    return "";
  }

  // Route by PR author, never by the actor who pushed or re-ran the workflow.
  switch (pr.user?.login) {
    case "iskhakov":
      return "ILDAR_CLAUDE_CODE_OAUTH_TOKEN";
    case "joeyorlando":
      return "JOEY_CLAUDE_CODE_OAUTH_TOKEN";
    case "piercypixel":
      return "MARK_CLAUDE_CODE_OAUTH_TOKEN";
    default:
      return "";
  }
}

module.exports = { selectToken };

if (require.main === module) {
  const secretName = selectToken({
    event: JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8")),
    eventName: process.env.GITHUB_EVENT_NAME,
    repository: process.env.GITHUB_REPOSITORY,
    repositoryOwner: process.env.GITHUB_REPOSITORY_OWNER,
  });
  appendFileSync(process.env.GITHUB_OUTPUT, `secret_name=${secretName}\n`);
  if (!secretName) console.log("Skipping review: PR is not eligible for a personal credential.");
}
