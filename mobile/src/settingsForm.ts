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
  greenhouse_board_tokens: string;
  lever_companies: string;
  ashby_orgs: string;
}

export function settingsToForm(settings: ServerSettings): SettingsForm {
  return {
    automation_enabled: settings.automation_enabled,
    quiet_hours_start: settings.quiet_hours_start,
    quiet_hours_end: settings.quiet_hours_end,
    max_dry_runs_per_day: String(settings.max_dry_runs_per_day),
    max_applications_per_day: String(settings.max_applications_per_day),
    min_match_score: String(settings.min_match_score),
    cycle_interval_minutes: String(settings.cycle_interval_minutes),
    cycle_max_dry_runs: String(settings.cycle_max_dry_runs),
    cycle_max_prepare: String(settings.cycle_max_prepare),
    cycle_max_score: String(settings.cycle_max_score),
    target_keywords: settings.target_keywords.join(", "),
    target_locations: settings.target_locations.join(", "),
    excluded_titles: settings.excluded_titles.join(", "),
    excluded_locations: settings.excluded_locations.join(", "),
    blacklisted_companies: settings.blacklisted_companies.join(", "),
    greenhouse_board_tokens: settings.greenhouse_board_tokens.join(", "),
    lever_companies: settings.lever_companies.join(", "),
    ashby_orgs: settings.ashby_orgs.join(", "),
  };
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b.at(index));
}

/** The typed number, or undefined when it is not a number or didn't change. */
function changedNumber(text: string, current: number): number | undefined {
  const value = Number.parseInt(text, 10);
  return Number.isNaN(value) || value === current ? undefined : value;
}

/** The typed list, or undefined when it didn't change. */
function changedList(text: string, current: string[]): string[] | undefined {
  const value = parseList(text);
  return sameList(value, current) ? undefined : value;
}

function changedText(text: string, current: string): string | undefined {
  const value = text.trim();
  return value === current ? undefined : value;
}

/**
 * Only the fields that changed, in the server's PATCH shape. Only keys from
 * the server's editable subset can appear here; the server rejects anything
 * else (for example allow_live_submission) with 422 anyway.
 */
export function buildSettingsPatch(settings: ServerSettings, form: SettingsForm): SettingsPatch {
  const candidate: SettingsPatch = {
    automation_enabled: form.automation_enabled === settings.automation_enabled ? undefined : form.automation_enabled,
    quiet_hours_start: changedText(form.quiet_hours_start, settings.quiet_hours_start),
    quiet_hours_end: changedText(form.quiet_hours_end, settings.quiet_hours_end),
    max_dry_runs_per_day: changedNumber(form.max_dry_runs_per_day, settings.max_dry_runs_per_day),
    max_applications_per_day: changedNumber(form.max_applications_per_day, settings.max_applications_per_day),
    min_match_score: changedNumber(form.min_match_score, settings.min_match_score),
    cycle_interval_minutes: changedNumber(form.cycle_interval_minutes, settings.cycle_interval_minutes),
    cycle_max_dry_runs: changedNumber(form.cycle_max_dry_runs, settings.cycle_max_dry_runs),
    cycle_max_prepare: changedNumber(form.cycle_max_prepare, settings.cycle_max_prepare),
    cycle_max_score: changedNumber(form.cycle_max_score, settings.cycle_max_score),
    target_keywords: changedList(form.target_keywords, settings.target_keywords),
    target_locations: changedList(form.target_locations, settings.target_locations),
    excluded_titles: changedList(form.excluded_titles, settings.excluded_titles),
    excluded_locations: changedList(form.excluded_locations, settings.excluded_locations),
    blacklisted_companies: changedList(form.blacklisted_companies, settings.blacklisted_companies),
    greenhouse_board_tokens: changedList(form.greenhouse_board_tokens, settings.greenhouse_board_tokens),
    lever_companies: changedList(form.lever_companies, settings.lever_companies),
    ashby_orgs: changedList(form.ashby_orgs, settings.ashby_orgs),
  };
  const editable = new Set(settings.editable);
  // Drop unchanged fields and anything outside the server's editable subset.
  const entries = Object.entries(candidate).filter(([key, value]) => value !== undefined && editable.has(key));
  return Object.fromEntries(entries) as SettingsPatch;
}
