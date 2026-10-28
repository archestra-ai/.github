// Cloud-free routing probe. Never sends a live token or prints request headers.
import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import { channel } from "node:diagnostics_channel";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const [bundlePath, phase] = process.argv.slice(2);
const hosts = new Set(["api.github.com", "frontend.archestra.dev"]);
const emit = (data) => console.log(JSON.stringify({ phase, ...data }));
for (const name of ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]) {
  const value = process.env[name];
  if (!value) {
    emit({ proxy: name, configured: false });
    continue;
  }
  try {
    const url = new URL(value.includes("://") ? value : `http://${value}`);
    emit({ proxy: name, hostname: url.hostname, port: url.port || null });
  } catch {
    emit({ proxy: name, configured: true, validUrl: false });
  }
}
const noProxy = (process.env.no_proxy || process.env.NO_PROXY || "")
  .toLowerCase().split(",").map((value) => value.trim()).filter(Boolean);
for (const hostname of hosts) {
  emit({ noProxy: hostname, matches: noProxy.some((value) =>
    value === "*" || value === hostname || hostname.endsWith(`.${value.replace(/^\./, "")}`)) });
  try {
    emit({ dns: hostname, addresses: await lookup(hostname, { all: true }) });
  } catch (error) {
    emit({ dns: hostname, code: error.code });
  }
}
channel("undici:client:connected").subscribe(({ connectParams, socket }) => {
  if (!hosts.has(connectParams.hostname)) return;
  emit({
    transport: "undici",
    hostname: connectParams.hostname,
    destination: socket.remoteAddress,
    port: socket.remotePort,
    authorized: socket.authorized,
    certificateNames: socket.getPeerCertificate?.().subjectaltname,
  });
});
channel("undici:client:connectError").subscribe(({ connectParams, error }) => {
  if (!hosts.has(connectParams.hostname)) return;
  emit({ transport: "undici", hostname: connectParams.hostname, code: error.code });
});

// Export only the client factory from the exact published bundle. The action's
// entry point is not run: this probe cannot sign in, post a review, or launch a run.
let source = await readFile(bundlePath, "utf8");
if (createHash("sha256").update(source).digest("hex") !==
    "0629f51d2f0429d4dfdcc08be90c46e2c6a4b22c1149251849f950529ee93486") {
  throw new Error("Unexpected review action bundle");
}
const entry = /\nmain\(\)\.catch\(\(error\d*\) => \{\n  setFailed\(`The action stopped with an error: \$\{errorMessage\(error\d*\)\}`\);\n\}\);/;
if (!entry.test(source)) throw new Error("Review action entry point changed");
source = source.replace(entry, "\nexport { createOctokit };");
const probePath = `${bundlePath}.probe.mjs`;
await writeFile(probePath, source);
const { createOctokit } = await import(pathToFileURL(probePath));

if (phase === "after") {
  const response = await fetch("https://frontend.archestra.dev/auth/sign-in", {
    redirect: "error", signal: AbortSignal.timeout(15_000),
  });
  emit({ client: "native-staging", status: response.status });
  if (response.status !== 200) throw new Error("Staging fixture did not answer");
  await response.arrayBuffer();
}
for (const client of ["native-github", "bundled-octokit"]) {
  try {
    const response = client === "native-github"
      ? await fetch("https://api.github.com/rate_limit", {
          redirect: "error", signal: AbortSignal.timeout(15_000),
        })
      : await createOctokit("synthetic-unused-token", { retryDelaysMs: [] })
          .request("GET /rate_limit", { request: { signal: AbortSignal.timeout(15_000) } });
    emit({ client, status: response.status });
    if (response.arrayBuffer) await response.arrayBuffer();
  } catch (error) {
    // The synthetic credential intentionally gets HTTP 401. A GitHub response
    // proves TLS reached GitHub; a transport error must still fail the test.
    emit({ client, status: error.status, code: error.cause?.cause?.code ?? error.cause?.code ?? error.code });
    if (error.status !== 401) process.exitCode = 1;
  }
}
