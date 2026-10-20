/** Pure helpers for the Materials screen. */
import type { Material } from "@/api/types";

export type MaterialKind = Material["kind"];

export const MATERIAL_KINDS: MaterialKind[] = ["resume", "cover_letter"];

export function kindLabel(kind: MaterialKind): string {
  return kind === "resume" ? "Résumé" : "Cover letter";
}

/** Newest version of each kind plus the older ones, newest first. */
export function byKind(items: Material[], kind: MaterialKind): { latest?: Material; older: Material[] } {
  const versions = items.filter((item) => item.kind === kind).sort((a, b) => b.version - a.version);
  return { latest: versions.at(0), older: versions.slice(1) };
}

export function shortHash(hash: string | null | undefined): string {
  return hash ? hash.slice(0, 12) : "—";
}

export type StatusTone = "success" | "warning" | "danger" | "muted";

export function statusTone(status: Material["status"]): StatusTone {
  switch (status) {
    case "approved":
      return "success";
    case "draft":
      return "warning";
    case "rejected":
      return "danger";
    default:
      return "muted";
  }
}

export function pendingDrafts(items: Material[]): Material[] {
  return items.filter((item) => item.status === "draft");
}

/** Short explanation shown above the materials list. */
export function materialsSummary(items: Material[]): string {
  const resume = byKind(items, "resume").latest;
  if (!resume) return "No materials yet. Generate drafts from your verified profile.";
  const drafts = pendingDrafts(items).length;
  if (drafts) return `${drafts} draft${drafts === 1 ? "" : "s"} waiting for your approval. Nothing is attached until you approve it.`;
  if (items.some((item) => item.kind === "resume" && item.status === "approved")) {
    return "An approved résumé is on file; dry-runs record which approved version would be attached.";
  }
  return "No approved résumé. Regenerate to create new drafts.";
}
