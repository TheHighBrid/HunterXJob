import { StyleSheet, Text, View } from "react-native";

import { LIVE_SUBMISSION_COPY } from "@/safety";
import { useTheme } from "@/theme";

/** Always-visible reminder that the server cannot submit applications. */
export function LockBanner({ compact }: { compact?: boolean }) {
  const theme = useTheme();
  return (
    <View
      accessibilityRole="summary"
      style={[styles.banner, { backgroundColor: theme.success + "1A", borderColor: theme.success }]}
    >
      <Text style={[styles.title, { color: theme.success }]}>🔒 Live submission locked</Text>
      {compact ? null : <Text style={[styles.body, { color: theme.textMuted }]}>{LIVE_SUBMISSION_COPY}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { borderWidth: 1, borderRadius: 12, paddingVertical: 10, paddingHorizontal: 12, gap: 4 },
  title: { fontSize: 13, fontWeight: "800" },
  body: { fontSize: 12, lineHeight: 17 },
});
