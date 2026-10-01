import type { KillSwitchRequest } from "@/api/types";

/**
 * Client-side mirror of the server's safety rules, used to shape requests and
 * copy. The server enforces all of these regardless of what the app sends.
 */
export const LIVE_SUBMISSION_COPY =
  "Live submission is locked. HunterXJob only runs dry-runs: it fills forms in memory and never clicks submit.";

/** Engaging is always allowed. Disengaging needs an explicit confirmation. */
export function killSwitchRequest(engage: boolean, confirmed: boolean, note = "") {
  if (engage) return { engaged: true, confirm: false, note: note || "engaged from the phone" } satisfies KillSwitchRequest;
  if (!confirmed) return null;
  return { engaged: false, confirm: true, note: note || "disengaged from the phone" } satisfies KillSwitchRequest;
}

export const REVIEW_ACTION_COPY = {
  approve: {
    title: "Approve?",
    confirm: "Approve",
    body: "Approving never submits anything. At most it queues a dry-run for the next cycle.",
  },
  reject: {
    title: "Reject this job?",
    confirm: "Reject",
    body: "The job is marked rejected and won't be prepared or dry-run.",
  },
  resolve: {
    title: "Mark as resolved?",
    confirm: "Resolve",
    body: "Closes the task without changing the job (for example, you handled it yourself).",
  },
} as const;

export type ReviewActionName = keyof typeof REVIEW_ACTION_COPY;

/** Confirmation copy for a review action (a switch, so no dynamic property lookup). */
export function reviewActionCopy(name: ReviewActionName) {
  switch (name) {
    case "approve":
      return REVIEW_ACTION_COPY.approve;
    case "reject":
      return REVIEW_ACTION_COPY.reject;
    default:
      return REVIEW_ACTION_COPY.resolve;
  }
}
