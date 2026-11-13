import { writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const config = (await import(pathToFileURL(request.config).href)).default;
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("Expected a runtime configuration object");
  }
  if ("hooks" in config) throw new Error("Hooks are not supported");
  if (Object.keys(config).some((key) => !["agent", "sandbox", "output"].includes(key))) {
    throw new Error("Unexpected runtime configuration option");
  }
  if (!config.agent || !config.sandbox) throw new Error("Agent and sandbox are required");

  if (config.output !== undefined && (!config.output || typeof config.output !== "object" || Array.isArray(config.output))) {
    throw new Error("Expected an output definition");
  }
  const { run } = await import("bernstein-sandcastle-api");
  const result = await run({
    cwd: process.cwd(),
    agent: config.agent,
    sandbox: config.sandbox,
    prompt: request.prompt,
    branchStrategy: { type: "branch", branch: request.branch },
    logging: { type: "file", path: request.logFilePath },
    maxIterations: 1,
    output: config.output === undefined ? undefined : { ...config.output, maxRetries: 0 },
    signal: AbortSignal.timeout(request.timeoutMs),
  });
  const { branch, commits, logFilePath, output } = result;
  if (config.output !== undefined && output === undefined) {
    throw new Error("Expected structured output");
  }
  await writeFile(request.resultPath, JSON.stringify({ branch, commits, logFilePath, output }), {
    encoding: "utf8", flag: "wx", mode: 0o600,
  });
}

main().catch((error) => {
  // Runtime errors may include prompt or provider output. Keep diagnostics scoped.
  console.error(error.message === "Hooks are not supported" ? error.message : "Run failed");
  process.exitCode = 1;
});
