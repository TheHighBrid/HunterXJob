import { ApiError, createClient, type FetchLike } from "@/api/client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? "" : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A fetch stand-in that always answers with this status and JSON body. */
function respondWith(status: number, body: unknown): FetchLike {
  return () => Promise.resolve(jsonResponse(status, body));
}

/** A fetch stand-in that records each call as "METHOD url" plus its init. */
function recordingFetch(calls: { url: string; init?: RequestInit }[], status: number, body: unknown): FetchLike {
  return (url, init) => {
    calls.push({ url: String(url), init });
    return Promise.resolve(jsonResponse(status, body));
  };
}

describe("createClient", () => {
  const config = { baseUrl: "http://vm.ts.net:8011", apiKey: "k".repeat(40) };

  it("sends the API key header and parses JSON", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = createClient(() => config, recordingFetch(calls, 200, { ok: true, version: "0.4.0", auth: "api_key" }));
    await expect(client.authCheck()).resolves.toEqual({ ok: true, version: "0.4.0", auth: "api_key" });
    expect(calls[0].url).toBe("http://vm.ts.net:8011/api/auth/check");
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>;
    expect(headers["X-API-Key"]).toBe(config.apiKey);
  });

  it("maps 401 to a key error and surfaces FastAPI details", async () => {
    const unauthorized = createClient(() => config, respondWith(401, { detail: "missing or invalid X-API-Key header" }));
    await expect(unauthorized.settings()).rejects.toMatchObject({ kind: "http", status: 401, message: expect.stringMatching(/API key/) });

    const refused = createClient(() => config, respondWith(428, { detail: "disengaging the kill switch needs confirm=true" }));
    await expect(refused.setKillSwitch({ engaged: false, confirm: false, note: "" })).rejects.toThrow(/confirm=true/);

    const invalid = createClient(
      () => config,
      respondWith(422, { detail: [{ loc: ["body", "allow_live_submission"], msg: "Extra inputs are not permitted" }] })
    );
    await expect(invalid.updateSettings({ max_dry_runs_per_day: 3 })).rejects.toThrow(/Extra inputs/);
  });

  it("reports network failures and missing configuration", async () => {
    const offline = createClient(() => config, () => Promise.reject(new TypeError("Network request failed")));
    await expect(offline.health()).rejects.toMatchObject({ kind: "network" });
    const unset = createClient(() => ({ baseUrl: "", apiKey: "" }));
    await expect(unset.health()).rejects.toBeInstanceOf(ApiError);
  });

  it("treats a busy scheduler as not started instead of an error", async () => {
    const busy = createClient(() => config, respondWith(409, { started: false, reason: "a cycle is already running", status_url: null }));
    await expect(busy.runCycleNow()).resolves.toMatchObject({ started: false });
  });

  it("posts review actions without submitting anything client-side", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const body = { task: {}, action: "requeue", job_stage: "ready_to_apply", application_stage: "ready_to_apply", submitted: false, live_submission_locked: true };
    const client = createClient(() => config, recordingFetch(calls, 200, body));
    const result = await client.approveTask("abc");
    expect(result.submitted).toBe(false);
    expect(calls.map((call) => `${call.init?.method ?? "GET"} ${call.url}`)).toEqual(["POST http://vm.ts.net:8011/api/review-tasks/abc/approve"]);
  });
});
