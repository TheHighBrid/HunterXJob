import { compareVersions, testConnection } from "@/api/connectionTest";
import type { FetchLike } from "@/api/client";

const KEY = "k".repeat(40);

function server(options: { version?: string; auth?: string; keyOk?: boolean }): FetchLike {
  return async (url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ ok: true, version: options.version ?? "0.4.0", auth: options.auth ?? "api_key" }), { status: 200 });
    }
    if (url.endsWith("/api/auth/check")) {
      if (options.keyOk === false || headers["X-API-Key"] !== KEY) {
        return new Response(JSON.stringify({ detail: "missing or invalid X-API-Key header" }), { status: 401 });
      }
      return new Response(JSON.stringify({ ok: true, version: options.version ?? "0.4.0", auth: "api_key" }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  };
}

describe("testConnection", () => {
  it("passes every step against a healthy server", async () => {
    const report = await testConnection("vm.tail1234.ts.net:8011", KEY, server({}));
    expect(report.ok).toBe(true);
    expect(report.url).toBe("http://vm.tail1234.ts.net:8011");
    expect(report.steps.map((s) => s.status)).toEqual(["ok", "ok", "ok", "ok"]);
  });

  it("stops at a rejected key", async () => {
    const report = await testConnection("http://100.64.0.5:8011", KEY, server({ keyOk: false }));
    expect(report.ok).toBe(false);
    expect(report.steps.find((s) => s.id === "auth")?.status).toBe("fail");
    expect(report.steps.find((s) => s.id === "version")?.status).toBe("skipped");
  });

  it("explains a server without an API key configured", async () => {
    const report = await testConnection("http://100.64.0.5:8011", KEY, server({ auth: "misconfigured" }));
    expect(report.ok).toBe(false);
    expect(report.steps[1].detail).toMatch(/API_KEY/);
  });

  it("rejects servers older than the remote-control API", async () => {
    const report = await testConnection("http://100.64.0.5:8011", KEY, server({ version: "0.3.0" }));
    expect(report.ok).toBe(false);
    expect(report.steps.find((s) => s.id === "version")?.status).toBe("fail");
  });

  it("warns about plain http to a public address", async () => {
    const report = await testConnection("http://203.0.113.10:8011", KEY, server({}));
    expect(report.steps[0].status).toBe("warn");
  });

  it("compares versions numerically", () => {
    expect(compareVersions("0.10.0", "0.4.0")).toBeGreaterThan(0);
    expect(compareVersions("0.4.0", "0.4.0")).toBe(0);
    expect(compareVersions("0.3.9", "0.4.0")).toBeLessThan(0);
  });
});
