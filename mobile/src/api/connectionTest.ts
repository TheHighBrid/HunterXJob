import { createClient, describeError, type FetchLike } from "./client";
import { isPlainHttpPublic, normalizeServerUrl, validateApiKey } from "@/lib/url";

/** Oldest server version with the remote-control endpoints this app uses. */
export const MIN_SERVER_VERSION = "0.4.0";

export type StepStatus = "ok" | "warn" | "fail" | "skipped";

export interface ConnectionStep {
  id: "url" | "reach" | "auth" | "version";
  label: string;
  status: StepStatus;
  detail: string;
}

export interface ConnectionReport {
  ok: boolean;
  url?: string;
  version?: string;
  steps: ConnectionStep[];
}

function versionParts(version: string): number[] {
  const parts = version.split(/[.+-]/).slice(0, 3).map((part) => Number.parseInt(part, 10) || 0);
  return [...parts, 0, 0, 0].slice(0, 3);
}

export function compareVersions(a: string, b: string): number {
  const other = versionParts(b);
  const diff = versionParts(a).map((part, index) => part - (other.at(index) ?? 0)).find((d) => d !== 0);
  return diff ?? 0;
}

type StepId = ConnectionStep["id"];
type Client = ReturnType<typeof createClient>;

const STEP_LABELS = new Map<StepId, string>([
  ["url", "Server URL"],
  ["reach", "Server reachable"],
  ["auth", "API key accepted"],
  ["version", "Server version"],
]);
const STEP_ORDER: StepId[] = ["url", "reach", "auth", "version"];

function step(id: StepId, status: StepStatus, detail: string): ConnectionStep {
  return { id, label: STEP_LABELS.get(id) ?? id, status, detail };
}

/** The finished steps, padded with "skipped" for every step that didn't run. */
function finish(steps: ConnectionStep[], extra: Omit<ConnectionReport, "ok" | "steps">): ConnectionReport {
  const skipped = STEP_ORDER.slice(steps.length).map((id) => step(id, "skipped", ""));
  const all = [...steps, ...skipped];
  const ok = skipped.length === 0 && all.every((s) => s.status !== "fail");
  return { ok, ...extra, steps: all };
}

function urlStep(url: string): ConnectionStep {
  return isPlainHttpPublic(url)
    ? step("url", "warn", "Plain http:// to a public address: the API key would cross the internet unencrypted. Use Tailscale or https.")
    : step("url", "ok", url);
}

async function reachStep(client: Client): Promise<{ step: ConnectionStep; version?: string }> {
  try {
    const health = await client.health();
    if (health.auth === "misconfigured") {
      return {
        version: health.version,
        step: step("reach", "fail", "Reached the server, but its API_KEY is not set up. Run ./hunterx doctor on the server."),
      };
    }
    return { version: health.version, step: step("reach", "ok", `HunterXJob ${health.version} (auth: ${health.auth})`) };
  } catch (err) {
    return { step: step("reach", "fail", describeError(err)) };
  }
}

async function authStep(client: Client, keyError: string | null): Promise<ConnectionStep> {
  if (keyError) return step("auth", "fail", keyError);
  try {
    await client.authCheck();
    return step("auth", "ok", "Authenticated");
  } catch (err) {
    return step("auth", "fail", describeError(err));
  }
}

function versionStep(version: string | undefined): ConnectionStep {
  const tooOld = compareVersions(version ?? "0", MIN_SERVER_VERSION) < 0;
  return tooOld
    ? step("version", "fail", `Server ${version} is older than ${MIN_SERVER_VERSION}. Update the server (git pull, ./hunterx install).`)
    : step("version", "ok", `${version}`);
}

/** Walk through URL -> reachability -> API key -> version, stopping at the first hard failure. */
export async function testConnection(rawUrl: string, rawKey: string, fetchImpl?: FetchLike): Promise<ConnectionReport> {
  const url = normalizeServerUrl(rawUrl);
  if (!url.ok) return finish([step("url", "fail", url.error)], {});

  const steps = [urlStep(url.url)];
  const client = createClient(() => ({ baseUrl: url.url, apiKey: rawKey.trim() }), fetchImpl);
  const reach = await reachStep(client);
  steps.push(reach.step);
  const extra = reach.version ? { url: url.url, version: reach.version } : { url: url.url };
  if (reach.step.status === "fail") return finish(steps, extra);

  const auth = await authStep(client, validateApiKey(rawKey));
  steps.push(auth);
  if (auth.status === "fail") return finish(steps, extra);

  steps.push(versionStep(reach.version));
  return finish(steps, extra);
}
