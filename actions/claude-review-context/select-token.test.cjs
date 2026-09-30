const assert = require("node:assert/strict");
const { mkdtempSync, readFileSync, writeFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { spawnSync } = require("node:child_process");
const { test } = require("node:test");
const { selectToken } = require("./select-token.cjs");

function context(author = "joeyorlando", repository = "archestra-ai/archestra") {
  return {
    eventName: "pull_request",
    repository,
    repositoryOwner: "archestra-ai",
    event: {
      action: "opened",
      sender: { login: "someone-else" },
      pull_request: {
        user: { login: author },
        title: "Fix request handling",
        draft: false,
        base: { repo: { full_name: repository } },
        head: { repo: { full_name: repository } },
      },
    },
  };
}

test("routes each author's token in both repositories, independently of the event sender", () => {
  for (const repository of ["archestra-ai/archestra", "archestra-ai/OpenAPPA"]) {
    for (const [author, secret] of [
      ["iskhakov", "ILDAR_CLAUDE_CODE_OAUTH_TOKEN"],
      ["joeyorlando", "JOEY_CLAUDE_CODE_OAUTH_TOKEN"],
      ["piercypixel", "MARK_CLAUDE_CODE_OAUTH_TOKEN"],
    ]) assert.equal(selectToken(context(author, repository)), secret);
  }
});

test("does not fall back to another user's credential for unconfigured authors", () => {
  for (const author of ["unknown", "dependabot[bot]", "constructor", "__proto__", ""]) {
    const value = context(author);
    value.event.sender.login = "joeyorlando";
    assert.equal(selectToken(value), "");
  }
});

test("accepts each configured PR lifecycle event", () => {
  for (const action of ["opened", "synchronize", "ready_for_review", "reopened"]) {
    const value = context();
    value.event.action = action;
    assert.equal(selectToken(value), "JOEY_CLAUDE_CODE_OAUTH_TOKEN");
  }
});

test("rejects drafts, WIP, forks, other organizations, and non-PR triggers", () => {
  const changes = [
    c => { c.event.pull_request.draft = true; },
    c => { c.event.pull_request.title = "Fix [WIP]"; },
    c => { c.event.pull_request.title = "[wip] Fix"; },
    c => { c.event.pull_request.head.repo.full_name = "contributor/fork"; },
    c => { c.event.pull_request.head.repo = null; },
    c => { c.event.pull_request.base.repo.full_name = "other/repo"; },
    c => { c.repositoryOwner = "other"; },
    c => { c.eventName = "pull_request_target"; },
    c => { c.eventName = "issue_comment"; },
    c => { c.eventName = "workflow_dispatch"; },
    c => { c.event.action = "closed"; },
    c => { delete c.event.pull_request; },
  ];
  for (const change of changes) {
    const value = context();
    change(value);
    assert.equal(selectToken(value), "");
  }
});

test("CLI writes only a secret name to the Actions output file", () => {
  const directory = mkdtempSync(join(tmpdir(), "claude-review-"));
  try {
    for (const author of ["joeyorlando", "unknown"]) {
      const value = context(author);
      const eventPath = join(directory, "event.json");
      const outputPath = join(directory, "output");
      writeFileSync(eventPath, JSON.stringify(value.event));
      writeFileSync(outputPath, "");
      const result = spawnSync(process.execPath, [join(__dirname, "select-token.cjs")], {
        env: {
          ...process.env,
          GITHUB_EVENT_PATH: eventPath,
          GITHUB_OUTPUT: outputPath,
          GITHUB_EVENT_NAME: value.eventName,
          GITHUB_REPOSITORY: value.repository,
          GITHUB_REPOSITORY_OWNER: value.repositoryOwner,
        },
        encoding: "utf8",
      });
      assert.equal(result.status, 0, result.stderr);
      assert.equal(readFileSync(outputPath, "utf8"), `secret_name=${selectToken(value)}\n`);
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
