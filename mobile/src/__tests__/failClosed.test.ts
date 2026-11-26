import { ApiError, createClient, type FetchLike } from "@/api/client";

const config = { baseUrl: "http://vm.ts.net:8011", apiKey: "k".repeat(40) };

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? "" : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function recordingFetch(calls: { url: string; init?: RequestInit }[], status: number, body: unknown): FetchLike {
  return (url, init) => {
    calls.push({ url: String(url), init });
    return Promise.resolve(jsonResponse(status, body));
  };
}

const dryRunBody = { job_id: "abc", status: "dry_run_verified", submitted: false, platform: "greenhouse" };
const batchBody = {
  started_at: "2026-11-26T19:00:00Z",
  finished_at: "2026-11-26T19:01:00Z",
  aborted_by_kill_switch: false,
  candidates_considered: 0,
  shortlisted: 0,
  submitted_live: 0,
  verified_dry_runs: 0,
  skipped_duplicates: 0,
  errors: [],
};

describe("mobile fail-closed live runs", () => {
  it("defaults autonomous calls to an explicit dry run", async () => {
    const jobCalls: { url: string; init?: RequestInit }[] = [];
    const jobs = createClient(() => config, recordingFetch(jobCalls, 200, dryRunBody));
    await jobs.runJobAutonomous("abc");
    expect(jobCalls).toHaveLength(1);
    expect(jobCalls[0].init?.method).toBe("POST");
    expect(jobCalls[0].url).toContain("/api/jobs/abc/run-autonomous?");
    expect(jobCalls[0].url).toContain("dry_run=true");
    expect(jobCalls[0].url).not.toContain("dry_run=false");

    const batchCalls: { url: string; init?: RequestInit }[] = [];
    const batch = createClient(() => config, recordingFetch(batchCalls, 200, batchBody));
    await batch.runAutonomousBatch();
    expect(batchCalls).toHaveLength(1);
    expect(batchCalls[0].init?.method).toBe("POST");
    expect(batchCalls[0].url).toBe("http://vm.ts.net:8011/api/autonomous/run?live=false");
  });

  it("refuses a live path without the exact phrase and sends no request", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = createClient(() => config, recordingFetch(calls, 200, { submitted: true }));

    await expect(client.runJobAutonomous("abc", false)).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.runJobAutonomous("abc", false, "")).rejects.toBeInstanceOf(ApiError);
    await expect(client.runJobAutonomous("abc", false, "submit live")).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.runJobAutonomous("abc", false, "SUBMIT")).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.runAutonomousBatch(true)).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.runAutonomousBatch(true, "YES")).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.executeApply("app-1")).rejects.toThrow(/SUBMIT LIVE/);
    await expect(client.executeApply("app-1", "submit live")).rejects.toThrow(/SUBMIT LIVE/);

    expect(calls).toHaveLength(0);
  });

  it("sends an explicit live request only after the exact phrase", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = createClient(
      () => config,
      recordingFetch(calls, 200, { submitted: true, status: "submitted", job_id: "abc", platform: "greenhouse", application_id: "app-1" })
    );

    await client.runJobAutonomous("abc", false, "SUBMIT LIVE");
    expect(calls[0].url).toContain("dry_run=false");
    expect(calls[0].url).not.toContain("dry_run=true");

    await client.runAutonomousBatch(true, "SUBMIT LIVE");
    expect(calls[1].url).toBe("http://vm.ts.net:8011/api/autonomous/run?live=true");

    await client.executeApply("app-1", "SUBMIT LIVE");
    expect(calls[2].url).toBe("http://vm.ts.net:8011/api/applications/app-1/apply");
    expect(calls[2].init?.method).toBe("POST");
  });
});

describe("429 RateLimitedError", () => {
  it("parses a Retry-After delay in seconds", async () => {
    const client = createClient(() => config, () => Promise.resolve(jsonResponse(429, { detail: "rate limited" }, { "Retry-After": "30" })));
    await expect(client.health()).rejects.toMatchObject({
      name: "RateLimitedError",
      status: 429,
      kind: "http",
      retryAfterSeconds: 30,
      message: expect.stringMatching(/30 seconds/),
    });
  });

  it("parses a Retry-After HTTP date", async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-11-26T19:00:00.000Z"));
    try {
      const client = createClient(
        () => config,
        () => Promise.resolve(jsonResponse(429, {}, { "Retry-After": "Thu, 26 Nov 2026 19:01:30 GMT" }))
      );
      await expect(client.health()).rejects.toMatchObject({
        name: "RateLimitedError",
        status: 429,
        retryAfterSeconds: 90,
        message: expect.stringMatching(/90 seconds/),
      });
    } finally {
      jest.useRealTimers();
    }
  });
});
