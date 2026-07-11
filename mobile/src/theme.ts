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

/** Status badge colors, keyed by ApplicationStatus. Kept here so every
 * screen that renders a status badge agrees on the same palette. */
export function statusColor(theme: Theme, status: string): string {
  switch (status) {
    case "applied":
      return theme.info;
    case "queued":
    case "discovered":
      return theme.textMuted;
    case "interview":
      return theme.success;
    case "offer":
      return theme.success;
    case "blocked":
    case "rejected":
      return theme.danger;
    case "needs_review":
      return theme.warning;
    case "withdrawn":
      return theme.textFaint;
    default:
      return theme.textMuted;
  }
}

export function matchScoreColor(theme: Theme, score: number | null): string {
  if (score === null) return theme.textFaint;
  if (score >= 75) return theme.success;
  if (score >= 50) return theme.warning;
  return theme.danger;
}
