import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/theme";
import { useSubscription } from "@/store/subscription";

/** Status banner reflecting live submission and autonomy capabilities. */
export function LockBanner({ compact }: { compact?: boolean }) {
  const theme = useTheme();
  const plan = useSubscription((s) => s.plan);
  const isPro = plan === "pro";

  if (isPro) {
    return (
      <View
        accessibilityRole="summary"
        style={[styles.banner, { backgroundColor: theme.primary + "1A", borderColor: theme.primary }]}
      >
        <Text style={[styles.title, { color: theme.primary }]}>⚡ Live submission unlocked (Pro Tier)</Text>
        {compact ? null : (
          <Text style={[styles.body, { color: theme.textMuted }]}>
            Autonomous live submission is unlocked. Applications can be submitted directly with verified evidence proofs.
          </Text>
        )}
      </View>
    );
  }

  return (
    <View
      accessibilityRole="summary"
      style={[styles.banner, { backgroundColor: theme.success + "1A", borderColor: theme.success }]}
    >
      <Text style={[styles.title, { color: theme.success }]}>🔒 Live submission locked (Free Tier)</Text>
      {compact ? null : (
        <Text style={[styles.body, { color: theme.textMuted }]}>
          Live submission is locked on Free tier. Activate Pro in Settings with license key HUNTERX-PRO-2026 to unlock autonomous live submission.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { borderWidth: 1, borderRadius: 12, paddingVertical: 10, paddingHorizontal: 12, gap: 4 },
  title: { fontSize: 13, fontWeight: "800" },
  body: { fontSize: 12, lineHeight: 17 },
});
