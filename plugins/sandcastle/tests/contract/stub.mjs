import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";

export async function run(options) {
  const { branchStrategy, logging, maxIterations, output, signal, cwd } = options;
  assert.equal(cwd, process.cwd());
  assert.equal(branchStrategy.type, "branch");
  assert.equal(logging.type, "file");
  assert.equal(maxIterations, 1);
  assert.equal(output.maxRetries, 0);
  assert.ok(signal instanceof AbortSignal);
  assert.equal(Object.hasOwn(options, "hooks"), false);
  assert.equal(options.prompt, "task input");

  const { mode, invocations, source } = JSON.parse(readFileSync(process.env.TEST_SCENARIO));
  appendFileSync(invocations, branchStrategy.branch + "\n");
  writeFileSync(logging.path, "stub run log\n");
  const git = (...args) => execFileSync(
    "git", ["-c", "user.name=Contract Test", "-c", "user.email=contract@example.invalid", ...args],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  ).trim();
  const base = git("rev-parse", "HEAD");
  const target = git("symbolic-ref", "HEAD");
  const branch = branchStrategy.branch;
  assert.equal(git("rev-parse", `refs/heads/${branch}`), base);
  assert.equal(git("remote"), "");

  if (mode === "error") throw new Error("Stub error");
  if (mode === "timeout") await new Promise((resolve) => setTimeout(resolve, 60_000));
  if (mode === "empty") return { branch, commits: [], logFilePath: logging.path, output: {} };

  const tree = git("rev-parse", "HEAD^{tree}");
  const first = git("commit-tree", tree, "-p", base, "-m", "First");
  const tip = git("commit-tree", tree, "-p", first, "-m", "Second");
  git("update-ref", `refs/heads/${branch}`, tip);
  if (mode === "target") git("update-ref", target, tip);
  if (mode === "source-target") git("-C", source, "update-ref", target, git("-C", source, "rev-parse", "HEAD^"));

  return {
    branch: mode === "branch" ? "unexpected" : branch,
    commits: [{ sha: mode === "commits" ? base : first }, { sha: tip }],
    logFilePath: mode === "log" ? `${logging.path}.other` : logging.path,
    output: { summary: "from stub" },
  };
}
