import type { ServerSettings, SettingsPatch } from "@/api/types";
import { parseList } from "@/utils/format";

/** Editable copy of the server settings, as typed into text inputs. */
export interface SettingsForm {
  automation_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  max_dry_runs_per_day: string;
  max_applications_per_day: string;
  min_match_score: string;
  cycle_interval_minutes: string;
  cycle_max_dry_runs: string;
  cycle_max_prepare: string;
  cycle_max_score: string;
  target_keywords: string;
  target_locations: string;
  excluded_titles: string;
  excluded_locations: string;
  blacklisted_companies: string;
}

const NUMBER_KEYS = [
  "max_dry_runs_per_day",
  "max_applications_per_day",
  "min_match_score",
  "cycle_interval_minutes",
  "cycle_max_dry_runs",
  "cycle_max_prepare",
  "cycle_max_score",
] as const;

const LIST_KEYS = ["target_keywords", "target_locations", "excluded_titles", "excluded_locations", "blacklisted_companies"] as const;

export function settingsToForm(settings: ServerSettings): SettingsForm {
  const form = {
    automation_enabled: settings.automation_enabled,
    quiet_hours_start: settings.quiet_hours_start,
    quiet_hours_end: settings.quiet_hours_end,
  } as SettingsForm;
  for (const key of NUMBER_KEYS) form[key] = String(settings[key]);
  for (const key of LIST_KEYS) form[key] = settings[key].join(", ");
  return form;
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/**
 * Only the fields that changed, in the server's PATCH shape. Only keys from
 * the server's editable subset can appear here; the server rejects anything
 * else (for example allow_live_submission) with 422 anyway.
 */
export function buildSettingsPatch(settings: ServerSettings, form: SettingsForm): SettingsPatch {
  const patch: SettingsPatch = {};
  if (form.automation_enabled !== settings.automation_enabled) patch.automation_enabled = form.automation_enabled;
  if (form.quiet_hours_start.trim() !== settings.quiet_hours_start) patch.quiet_hours_start = form.quiet_hours_start.trim();
  if (form.quiet_hours_end.trim() !== settings.quiet_hours_end) patch.quiet_hours_end = form.quiet_hours_end.trim();
  for (const key of NUMBER_KEYS) {
    const value = Number.parseInt(form[key], 10);
    if (!Number.isNaN(value) && value !== settings[key]) patch[key] = value;
  }
  for (const key of LIST_KEYS) {
    const value = parseList(form[key]);
    if (!sameList(value, settings[key])) patch[key] = value;
  }
  const editable = new Set(settings.editable);
  return Object.fromEntries(Object.entries(patch).filter(([key]) => editable.has(key))) as SettingsPatch;
}
