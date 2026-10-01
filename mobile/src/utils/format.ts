export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function formatRelativeToNow(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffMin = Math.round((date.getTime() - now) / 60000);
  const abs = Math.abs(diffMin);
  if (abs < 1) return "just now";
  if (abs < 60) return diffMin > 0 ? `in ${abs}m` : `${abs}m ago`;
  const hours = Math.round(abs / 60);
  if (hours < 24) return diffMin > 0 ? `in ${hours}h` : `${hours}h ago`;
  const days = Math.round(hours / 24);
  return diffMin > 0 ? `in ${days}d` : `${days}d ago`;
}

/** "materials_generated" -> "Materials generated" */
export function humanize(input: string | null | undefined): string {
  if (!input) return "—";
  const text = input.replace(/[._]/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : String(Math.round(score));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Split a comma/newline separated list typed by the user. */
export function parseList(text: string): string[] {
  const seen: string[] = [];
  for (const raw of text.split(/[,\n]/)) {
    const item = raw.trim();
    if (item && !seen.includes(item)) seen.push(item);
  }
  return seen;
}
