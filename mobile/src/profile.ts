/**
 * Pure helpers for the Profile screen: fact labels, grouping and the small
 * field editor. Facts are edited as flat text fields; the server validates
 * every edit against the category schema and un-verifies edited facts
 * unless the owner explicitly saves-and-verifies.
 */
import type { FactCategory, ProfileFact } from "@/api/types";

export const CATEGORY_ORDER: FactCategory[] = [
  "contact",
  "summary",
  "work_authorization",
  "employment",
  "achievement",
  "education",
  "skill",
  "certification",
  "language",
  "project",
];

const CATEGORY_LABELS = new Map<string, string>([
  ["contact", "Contact"],
  ["summary", "Summary"],
  ["work_authorization", "Work authorization"],
  ["employment", "Employment"],
  ["achievement", "Achievements"],
  ["education", "Education"],
  ["skill", "Skills"],
  ["certification", "Certifications"],
  ["language", "Languages"],
  ["project", "Projects"],
]);

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS.get(category) ?? category;
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((item) => text(item)).filter(Boolean).join(", ");
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function field(fact: ProfileFact, name: string): string {
  return text(new Map(Object.entries(fact.data)).get(name));
}

function joined(parts: string[], separator = " · "): string {
  return parts.filter(Boolean).join(separator);
}

export function dateRange(fact: ProfileFact): string {
  const start = field(fact, "start");
  const end = field(fact, "current") === "true" ? "present" : field(fact, "end");
  if (!start && !end) return "";
  return `${start || "?"} – ${end || "?"}`;
}

function authorizationLine(fact: ProfileFact): string {
  const authorized = field(fact, "authorized") === "true" ? "Authorized" : "Not authorized";
  const sponsorship = field(fact, "requires_sponsorship");
  let needs = "";
  if (sponsorship === "true") needs = "needs sponsorship";
  else if (sponsorship === "false") needs = "no sponsorship needed";
  return joined([`${field(fact, "country")}: ${authorized}`, needs], ", ");
}

/** One-line title for a fact card. */
export function factTitle(fact: ProfileFact): string {
  switch (fact.category) {
    case "contact":
      return joined([field(fact, "field").replace(/_/g, " "), field(fact, "value")], ": ");
    case "summary":
    case "achievement":
      return field(fact, "text");
    case "work_authorization":
      return authorizationLine(fact);
    case "employment":
      return joined([field(fact, "title"), field(fact, "employer")], " — ");
    case "education":
      return joined([joined([field(fact, "degree"), field(fact, "field_of_study")], ", "), field(fact, "institution")], " — ");
    default:
      return field(fact, "name") || fact.key;
  }
}

/** Secondary line: dates, places, metrics, issuers. */
export function factSubtitle(fact: ProfileFact): string {
  switch (fact.category) {
    case "employment":
    case "education":
      return joined([dateRange(fact), field(fact, "location")]);
    case "achievement":
      return joined([field(fact, "metrics") && `Metrics: ${field(fact, "metrics")}`, field(fact, "skills")]);
    case "skill":
      return joined([field(fact, "aliases") && `aka ${field(fact, "aliases")}`, field(fact, "level")]);
    case "certification":
      return joined([field(fact, "issuer"), field(fact, "date")]);
    case "language":
      return field(fact, "proficiency");
    case "project":
      return joined([field(fact, "role"), field(fact, "skills")]);
    default:
      return "";
  }
}

export interface FactGroup {
  category: string;
  label: string;
  facts: ProfileFact[];
  unverified: number;
}

export function groupFacts(facts: ProfileFact[]): FactGroup[] {
  const groups = new Map<string, ProfileFact[]>();
  for (const fact of facts) groups.set(fact.category, [...(groups.get(fact.category) ?? []), fact]);
  const order = [...CATEGORY_ORDER, ...[...groups.keys()].filter((key) => !CATEGORY_ORDER.some((known) => known === key))];
  return order
    .filter((category) => groups.has(category))
    .map((category) => {
      const items = groups.get(category) ?? [];
      return { category, label: categoryLabel(category), facts: items, unverified: items.filter((item) => !item.verified).length };
    });
}

export type FieldKind = "text" | "list" | "boolean" | "number";

export interface EditableField {
  name: string;
  kind: FieldKind;
  value: string;
  nullable: boolean;
}

const NULLABLE = new Set(["start", "end", "date", "expires", "employment", "project", "requires_sponsorship", "years"]);
const NUMBERS = new Set(["years"]);
const LISTS = new Set(["aliases", "metrics", "skills"]);
const BOOLEANS = new Set(["authorized", "requires_sponsorship", "current"]);

function kindOf(name: string, value: unknown): FieldKind {
  if (Array.isArray(value) || LISTS.has(name)) return "list";
  if (typeof value === "boolean" || BOOLEANS.has(name)) return "boolean";
  if (typeof value === "number" || NUMBERS.has(name)) return "number";
  return "text";
}

/** Flatten a fact's data into editable text fields (lists as comma-separated). */
export function editableFields(data: Record<string, unknown>): EditableField[] {
  return Object.entries(data).map(([name, value]) => ({
    name,
    kind: kindOf(name, value),
    value: text(value),
    nullable: value === null || NULLABLE.has(name),
  }));
}

function parseField(item: EditableField, raw: string): unknown {
  const value = raw.trim();
  if (value === "" && item.nullable) return null;
  switch (item.kind) {
    case "list":
      return value
        .split(/[,\n]/)
        .map((part) => part.trim())
        .filter(Boolean);
    case "boolean":
      return value === "true";
    case "number": {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : value;
    }
    default:
      return value;
  }
}

/** Rebuild fact data from edited field strings; untouched fields keep their value. */
export function applyEdits(data: Record<string, unknown>, edits: Map<string, string>): Record<string, unknown> {
  const fields = editableFields(data);
  return Object.fromEntries(
    Object.entries(data).map(([name, original]) => {
      const edited = edits.get(name);
      const item = fields.find((candidate) => candidate.name === name);
      if (edited === undefined || !item) return [name, original];
      return [name, parseField(item, edited)];
    }),
  );
}

export function readinessCopy(ready: boolean, missing: string[]): string {
  if (ready) return "Ready: résumés and cover letters can be generated from verified facts.";
  return `Not ready for materials yet. Missing: ${missing.join(", ") || "verified facts"}.`;
}
