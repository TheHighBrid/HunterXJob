import { getConnection } from "@/store/connection";

import type {
  AuthCheck,
  Backups,
  Cycle,
  FactCategory,
  FactRemoved,
  FormPreview,
  Health,
  Job,
  JobDetail,
  JobMaterials,
  KillSwitch,
  KillSwitchRequest,
  MaterialAction,
  MaterialDetail,
  Profile,
  ProfileFact,
  ProfileImport,
  ProfileImportResult,
  ReviewAction,
  ReviewResolution,
  ReviewTask,
  ReviewTaskDetail,
  RunCycle,
  SchedulerStatus,
  ServerSettings,
  SettingsPatch,
  Summary,
} from "./types";

export type ApiErrorKind = "config" | "timeout" | "network" | "http" | "parse";

/** Thrown by every api.* call. Always carries a user-presentable message. */
export class ApiError extends Error {
  kind: ApiErrorKind;
  status?: number;

  constructor(message: string, kind: ApiErrorKind, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export interface ClientConfig {
  baseUrl: string;
  apiKey: string;
}

export type FetchLike = typeof fetch;

const DEFAULT_TIMEOUT_MS = 15000;

function detailText(body: unknown): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return "";
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}, ...]
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : ""))
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object" && "detail" in detail) return String((detail as { detail: unknown }).detail);
  return "";
}

export function errorForStatus(status: number, path: string, detail: string): ApiError {
  if (status === 401) return new ApiError("The server rejected the API key. Check it in Connection.", "http", status);
  if (status === 503) {
    return new ApiError(`The server's API authentication isn't configured${detail ? `: ${detail}` : ""}.`, "http", status);
  }
  return new ApiError(detail || `Request to ${path} failed (${status}).`, "http", status);
}

export function createClient(getConfig: () => ClientConfig, fetchImpl: FetchLike = fetch) {
  async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    const { baseUrl, apiKey } = getConfig();
    if (!baseUrl) throw new ApiError("Set the server URL in Connection first.", "config");

    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, timeoutMs);
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...(apiKey ? { "X-API-Key": apiKey } : {}),
          ...(init.headers ?? {}),
        },
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        throw new ApiError(`Timed out reaching ${baseUrl}. Is the server running and reachable (Tailscale on)?`, "timeout");
      }
      throw new ApiError(`Couldn't reach ${baseUrl}. Check the URL and that this phone is on your tailnet.`, "network");
    } finally {
      clearTimeout(timer);
    }

    let body: unknown;
    const text = await response.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        if (response.ok) throw new ApiError(`Received a malformed response from ${path}.`, "parse");
      }
    }
    if (!response.ok) throw errorForStatus(response.status, path, detailText(body));
    return body as T;
  }

  function post<T>(path: string, payload?: unknown): Promise<T> {
    return request<T>(path, { method: "POST", body: payload === undefined ? undefined : JSON.stringify(payload) });
  }

  function put<T>(path: string, payload: unknown): Promise<T> {
    return request<T>(path, { method: "PUT", body: JSON.stringify(payload) });
  }

  return {
    request,
    health: () => request<Health>("/api/health"),
    authCheck: () => request<AuthCheck>("/api/auth/check"),

    settings: () => request<ServerSettings>("/api/settings"),
    updateSettings: (patch: SettingsPatch) =>
      request<ServerSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) }),

    summary: () => request<Summary>("/api/reports/summary"),

    jobs: (params: { q?: string; stage?: string; limit?: number } = {}) => {
      const query = new URLSearchParams();
      if (params.q) query.set("q", params.q);
      if (params.stage) query.set("stage", params.stage);
      query.set("limit", String(params.limit ?? 200));
      return request<Job[]>(`/api/jobs?${query.toString()}`);
    },
    job: (id: string) => request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`),
    previewForm: (id: string) => request<FormPreview>(`/api/jobs/${encodeURIComponent(id)}/form`, {}, 60000),
    unlinkDuplicate: (id: string) => post<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/unlink-duplicate`),
    checkLiveness: (id: string) => post<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/check-liveness`),

    reviewTasks: (status: "open" | "closed" | "all" = "open") => request<ReviewTask[]>(`/api/review-tasks?status=${status}`),
    reviewTask: (id: string) => request<ReviewTaskDetail>(`/api/review-tasks/${encodeURIComponent(id)}`),
    approveTask: (id: string) => post<ReviewAction>(`/api/review-tasks/${encodeURIComponent(id)}/approve`),
    rejectTask: (id: string) => post<ReviewAction>(`/api/review-tasks/${encodeURIComponent(id)}/reject`),
    resolveTask: (id: string, resolution: ReviewResolution = "resolved") =>
      post<ReviewAction>(`/api/review-tasks/${encodeURIComponent(id)}/resolve`, { resolution }),

    schedulerStatus: () => request<SchedulerStatus>("/api/scheduler/status"),
    cycles: (limit = 10) => request<Cycle[]>(`/api/scheduler/cycles?limit=${limit}`),
    pauseScheduler: () => post<SchedulerStatus>("/api/scheduler/pause", { note: "paused from the phone" }),
    resumeScheduler: () => post<SchedulerStatus>("/api/scheduler/resume", { note: "resumed from the phone" }),
    runCycleNow: async (): Promise<RunCycle> => {
      try {
        return await post<RunCycle>("/api/scheduler/run");
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) return { started: false, reason: "A cycle is already running.", status_url: null };
        throw err;
      }
    },

    killSwitch: () => request<KillSwitch>("/api/kill-switch"),
    setKillSwitch: (body: KillSwitchRequest) => post<KillSwitch>("/api/kill-switch", body),

    backups: () => request<Backups>("/api/backups"),

    profile: () => request<Profile>("/api/profile"),
    importProfile: (body: ProfileImport) => post<ProfileImportResult>("/api/profile/import", body),
    addFact: (category: FactCategory, data: Record<string, unknown>, verified = false) =>
      post<ProfileFact>("/api/profile/facts", { category, data, verified }),
    editFact: (id: string, data: Record<string, unknown>, verified = false) =>
      put<ProfileFact>(`/api/profile/facts/${encodeURIComponent(id)}`, { data, verified }),
    verifyFact: (id: string, verified: boolean) => post<ProfileFact>(`/api/profile/facts/${encodeURIComponent(id)}/verify`, { verified }),
    removeFact: (id: string) => post<FactRemoved>(`/api/profile/facts/${encodeURIComponent(id)}/remove`),

    jobMaterials: (jobId: string) => request<JobMaterials>(`/api/jobs/${encodeURIComponent(jobId)}/materials`),
    generateMaterials: (jobId: string) => request<JobMaterials>(`/api/jobs/${encodeURIComponent(jobId)}/materials/generate`, { method: "POST" }, 60000),
    material: (id: string) => request<MaterialDetail>(`/api/materials/${encodeURIComponent(id)}`),
    approveMaterial: (id: string, note = "") => post<MaterialAction>(`/api/materials/${encodeURIComponent(id)}/approve`, { note }),
    rejectMaterial: (id: string, note = "") => post<MaterialAction>(`/api/materials/${encodeURIComponent(id)}/reject`, { note }),
  };
}

export type Api = ReturnType<typeof createClient>;

/** The app-wide client, bound to the saved connection. */
export const api: Api = createClient(getConnection);

export function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
