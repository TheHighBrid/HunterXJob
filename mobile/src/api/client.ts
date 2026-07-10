import { getConnectionConfig } from "@/store/settings";
import type {
  ApplicationRecord,
  AutomationSettings,
  CreateApplicationPayload,
  HealthResponse,
  JobPosting,
  PatchApplicationPayload,
  Report,
} from "@/types";

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

const DEFAULT_TIMEOUT_MS = 12000;

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const { baseUrl, apiKey } = getConnectionConfig();

  if (!baseUrl) {
    throw new ApiError(
      "Backend URL isn't configured yet. Set it in Settings.",
      "config"
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
        ...(options.headers ?? {}),
      },
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new ApiError(
        `Timed out reaching ${baseUrl}. Check the backend URL and that it's running.`,
        "timeout"
      );
    }
    throw new ApiError(
      `Couldn't reach ${baseUrl}. Check the backend URL and your network connection.`,
      "network"
    );
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = `: ${body.detail}`;
    } catch {
      // response body wasn't JSON (or was empty) — ignore
    }
    const kind: ApiErrorKind = "http";
    if (response.status === 401 || response.status === 403) {
      throw new ApiError(
        `Authentication failed (${response.status}). Check your API key in Settings.`,
        kind,
        response.status
      );
    }
    throw new ApiError(
      `Request to ${path} failed (${response.status})${detail}`,
      kind,
      response.status
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(`Received a malformed response from ${path}.`, "parse");
  }
}

export const api = {
  getHealth: () => request<HealthResponse>("/api/health"),

  getSettings: () => request<AutomationSettings>("/api/settings"),
  updateSettings: (patch: Partial<AutomationSettings>) =>
    request<AutomationSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),

  getJobs: () => request<JobPosting[]>("/api/jobs"),

  getApplications: () => request<ApplicationRecord[]>("/api/applications"),
  createApplication: (payload: CreateApplicationPayload) =>
    request<ApplicationRecord>("/api/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateApplication: (id: string, patch: PatchApplicationPayload) =>
    request<ApplicationRecord>(`/api/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  getReports: () => request<Report[]>("/api/reports"),
  getLatestReport: () => request<Report>("/api/reports/latest"),
};

/** True for errors it makes sense to silently fall back to demo data for. */
export function isConnectivityError(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    (err.kind === "config" || err.kind === "timeout" || err.kind === "network")
  );
}

export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
