/** User-visible UI headers, timeline labels, and dry-run toast copy. */
export const QUESTION_STUDIO_TITLE = "Interactive Question Studio (HunterXJob)";
export const JOB_TIMELINE_TITLE = "Timeline";

/** Dry-run success toast: fields are rendered and verified; values are planned, never typed. */
export const DRY_RUN_VERIFIED_TITLE = "Dry-Run Verified ✓";

export function dryRunVerifiedBody(company: string): string {
  return `All fields on ${company} were rendered and verified. Values were planned; nothing was typed or submitted.`;
}
