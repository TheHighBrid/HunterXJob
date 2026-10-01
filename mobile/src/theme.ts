import { useColorScheme } from "react-native";

export interface Theme {
  scheme: "light" | "dark";
  background: string;
  surface: string;
  surfaceAlt: string;
  border: string;
  text: string;
  textMuted: string;
  textFaint: string;
  primary: string;
  primaryText: string;
  danger: string;
  warning: string;
  success: string;
  info: string;
}

const light: Theme = {
  scheme: "light",
  background: "#F4F5F7",
  surface: "#FFFFFF",
  surfaceAlt: "#ECEEF1",
  border: "#DDE1E6",
  text: "#12151A",
  textMuted: "#5B6472",
  textFaint: "#8A93A1",
  primary: "#2F6FED",
  primaryText: "#FFFFFF",
  danger: "#D64545",
  warning: "#B7791F",
  success: "#1E8E5A",
  info: "#2F6FED",
};

const dark: Theme = {
  scheme: "dark",
  background: "#0E1116",
  surface: "#171B22",
  surfaceAlt: "#1F242C",
  border: "#2B313B",
  text: "#F2F4F7",
  textMuted: "#A1AAB8",
  textFaint: "#6D7686",
  primary: "#5B93FF",
  primaryText: "#0B1220",
  danger: "#F27373",
  warning: "#E0AC4E",
  success: "#4FCB8C",
  info: "#5B93FF",
};

export function useTheme(): Theme {
  const scheme = useColorScheme();
  return scheme === "dark" ? dark : light;
}

const GOOD_STAGES = new Set(["shortlisted", "approved", "materials_generated", "materials_reviewed", "ready_to_apply", "validated", "form_filled"]);
const ATTENTION_STAGES = new Set(["review", "needs_review", "submission_uncertain", "applying", "preparing"]);
const BAD_STAGES = new Set(["rejected", "failed"]);

/** Badge color for a v2 pipeline stage, shared by every screen. */
export function stageColor(theme: Theme, stage: string | null | undefined): string {
  if (!stage) return theme.textFaint;
  if (GOOD_STAGES.has(stage)) return theme.success;
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
