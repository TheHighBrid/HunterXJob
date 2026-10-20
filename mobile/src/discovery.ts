/**
 * Pure helpers for discovery metadata on jobs: which ATS a posting came
 * from, duplicate links across boards, and posting liveness.
 */
import type { Job, JobDetail } from "@/api/types";

export type Tone = "success" | "warning" | "danger" | "info" | "muted";

export function sourceLabel(source: string | null | undefined): string {
  switch (source) {
    case "greenhouse":
      return "Greenhouse";
    case "lever":
      return "Lever";
    case "ashby":
      return "Ashby";
    case undefined:
    case null:
    case "":
      return "Unknown source";
    default:
      return source.charAt(0).toUpperCase() + source.slice(1);
  }
}

export interface LivenessInfo {
  label: string;
  tone: Tone;
  explanation: string;
}

/** Badge text and tone for a liveness status (null when the posting was never checked). */
export function livenessInfo(status: string | null | undefined): LivenessInfo | null {
  switch (status) {
    case "live":
      return { label: "Live", tone: "success", explanation: "The posting's own source confirmed it is open." };
    case "suspect":
      return {
        label: "May be closed",
        tone: "warning",
        explanation: "One check found the posting gone. It is only closed if a second check confirms it later.",
      };
    case "unknown":
      return {
        label: "Liveness unknown",
        tone: "muted",
        explanation: "Recent checks were inconclusive (network or server errors). Nothing is closed on that basis.",
      };
    case "closed":
      return { label: "Closed", tone: "danger", explanation: "Two checks confirmed the posting was removed. Its review tasks were closed." };
    default:
      return null;
  }
}

/** Short badge for duplicate/linked postings, or null when the role was posted once (or the stage badge already says it). */
export function linkedLabel(job: Pick<Job, "duplicate_of_id" | "linked_count" | "stage">): string | null {
  if (job.duplicate_of_id) return job.stage === "duplicate" ? null : "Duplicate";
  const count = job.linked_count ?? 0;
  if (count <= 0) return null;
  return count === 1 ? "1 linked posting" : `${count} linked postings`;
}

/** The liveness status worth a badge (a closed job's stage badge already says "Closed"). */
export function livenessBadgeStatus(job: Pick<Job, "liveness" | "stage">): string | null {
  if (job.stage === "closed") return null;
  return job.liveness;
}

export function matchMethodLabel(method: string | null | undefined): string {
  switch (method) {
    case "exact":
      return "same title and description";
    case "fuzzy":
      return "near-identical title and description";
    case "title_location":
      return "same title and location";
    default:
      return "linked";
  }
}

export type LivenessCheck = JobDetail["liveness_detail"]["checks"][number];

/** One readable line for a recorded liveness check. */
export function livenessLine(check: LivenessCheck): string {
  const status = check.http_status ? ` (HTTP ${check.http_status})` : "";
  const action = check.action && check.action !== "none" ? ` → ${check.action.replace(/_/g, " ")}` : "";
  return `${check.outcome}${status}: ${check.signal.replace(/_/g, " ")}${action}`;
}
