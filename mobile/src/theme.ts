import { useColorScheme } from "react-native";

export interface Theme {
  scheme: "light" | "dark";
  background: string;
  surface: string;
  surfaceAlt: string;
  elevated: string;
  border: string;
  borderSubtle: string;
  text: string;
  textMuted: string;
  textFaint: string;
  primary: string;
  primarySoft: string;
  primaryText: string;
  danger: string;
  warning: string;
  uncertain: string;
  success: string;
  info: string;
}

const light: Theme = {
  scheme: "light",
  background: "#EEF2F8",
  surface: "#FFFFFF",
  surfaceAlt: "#E4EAF3",
  elevated: "#F8FAFD",
  border: "#D2DCE9",
  borderSubtle: "#E5ECF4",
  text: "#0B111E",
  textMuted: "#4A566B",
  textFaint: "#7A879C",
  primary: "#3B5BDB",
  primarySoft: "#EBF0FF",
  primaryText: "#FFFFFF",
  danger: "#E03131",
  warning: "#D97706",
  uncertain: "#E8590C",
  success: "#0CA678",
  info: "#1C7ED6",
};

const dark: Theme = {
  scheme: "dark",
  background: "#070B12",
  surface: "#101723",
  surfaceAlt: "#182132",
  elevated: "#131C2B",
  border: "#233046",
  borderSubtle: "#1B2537",
  text: "#F3F6FC",
  textMuted: "#98A6BD",
  textFaint: "#63728A",
  primary: "#5C7CFA",
  primarySoft: "#1A2547",
  primaryText: "#FFFFFF",
  danger: "#FA5252",
  warning: "#F59F00",
  uncertain: "#FF7B39",
  success: "#20C997",
  info: "#4DABF7",
};

export function useTheme(): Theme {
  const scheme = useColorScheme();
  return scheme === "dark" ? dark : light;
}

const SUBMITTED_STAGES = new Set(["submitted", "confirmed"]);
const READY_STAGES = new Set(["shortlisted", "approved", "materials_generated", "materials_reviewed", "ready_to_apply", "validated", "form_filled"]);
const UNCERTAIN_STAGES = new Set(["submission_uncertain"]);
const ATTENTION_STAGES = new Set(["review", "needs_review", "applying", "preparing"]);
const BAD_STAGES = new Set(["rejected", "failed"]);

/** Badge color for a v2 pipeline stage, shared by every screen. */
export function stageColor(theme: Theme, stage: string | null | undefined): string {
  if (!stage) return theme.textFaint;
  if (SUBMITTED_STAGES.has(stage)) return theme.success;
  if (READY_STAGES.has(stage)) return theme.primary;
  if (UNCERTAIN_STAGES.has(stage)) return theme.uncertain;
  if (ATTENTION_STAGES.has(stage)) return theme.warning;
  if (BAD_STAGES.has(stage)) return theme.danger;
  if (stage === "withdrawn" || stage === "duplicate" || stage === "closed") return theme.textFaint;
  return theme.info;
}

/** Badge color for a scheduler cycle status. */
export function cycleColor(theme: Theme, status: string): string {
  if (status === "completed") return theme.success;
  if (status === "completed_with_errors" || status === "skipped" || status === "interrupted") return theme.warning;
  if (status === "running") return theme.info;
  return theme.danger;
}

export function matchScoreColor(theme: Theme, score: number | null | undefined): string {
  if (score === null || score === undefined) return theme.textFaint;
  if (score >= 75) return theme.success;
  if (score >= 50) return theme.warning;
  return theme.danger;
}
