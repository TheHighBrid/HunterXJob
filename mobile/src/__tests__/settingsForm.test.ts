import type { ServerSettings } from "@/api/types";
import { buildSettingsPatch, settingsToForm } from "@/settingsForm";

const settings: ServerSettings = {
  auth_mode: "api_key",
  application_mode: "dry_run",
  automation_enabled: false,
  live_submission: { locked: true, env_allow_live_submission: false, reason: "locked" },
  continuous_run_enabled: true,
  cycle_interval_minutes: 60,
  cycle_max_score: 50,
  cycle_max_prepare: 2,
  cycle_max_dry_runs: 3,
  max_dry_runs_per_day: 10,
  max_applications_per_day: 5,
  min_match_score: 60,
  quiet_hours_start: "23:00",
  quiet_hours_end: "07:00",
  timezone: "America/Toronto",
  target_locations: ["Ottawa", "Remote Canada"],
  target_keywords: ["fraud", "AML"],
  excluded_locations: [],
  excluded_titles: [],
  blacklisted_companies: [],
  sources: { greenhouse_boards: 2, lever_companies: 0, generic_feeds: 0 },
  llm_provider: "ollama",
  llm_fast_model: "llama3.2:1b",
  llm_quality_model: "llama3.2:3b",
  greenhouse_browser_verify: true,
  backup_interval_hours: 24,
  backup_retention: 14,
  editable: [
    "automation_enabled", "max_applications_per_day", "max_dry_runs_per_day", "min_match_score", "quiet_hours_start",
    "quiet_hours_end", "cycle_interval_minutes", "cycle_max_score", "cycle_max_prepare", "cycle_max_dry_runs",
    "target_locations", "target_keywords", "excluded_locations", "excluded_titles", "blacklisted_companies",
  ],
  overridden: [],
};

describe("buildSettingsPatch", () => {
  it("is empty when nothing changed", () => {
    expect(buildSettingsPatch(settings, settingsToForm(settings))).toEqual({});
  });

  it("sends only changed, editable fields", () => {
    const form = { ...settingsToForm(settings), max_dry_runs_per_day: "4", target_keywords: "fraud, AML, KYC", automation_enabled: true };
    expect(buildSettingsPatch(settings, form)).toEqual({ max_dry_runs_per_day: 4, target_keywords: ["fraud", "AML", "KYC"], automation_enabled: true });
  });

  it("drops fields the server does not list as editable", () => {
    const locked = { ...settings, editable: ["min_match_score"] };
    const form = { ...settingsToForm(locked), automation_enabled: true, min_match_score: "70" };
    const patch = buildSettingsPatch(locked, form);
    expect(patch).toEqual({ min_match_score: 70 });
    expect(Object.keys(patch)).not.toContain("allow_live_submission");
  });
});
