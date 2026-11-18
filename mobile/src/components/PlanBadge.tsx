import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useSubscription, MAX_DAILY_FREE_APPLICATIONS } from "@/store/subscription";
import { useTheme } from "@/theme";

interface PlanBadgeProps {
  onUpgradePress?: () => void;
}

export function PlanBadge({ onUpgradePress }: PlanBadgeProps) {
  const theme = useTheme();
  const plan = useSubscription((s) => s.plan);
  const remaining = useSubscription((s) => s.remainingToday());
  const isPro = plan === "pro";

  return (
    <View style={[styles.container, { backgroundColor: isPro ? theme.surfaceAlt : theme.surface, borderColor: isPro ? theme.primary : theme.border }]}>
      <View style={styles.left}>
        <View style={[styles.pill, { backgroundColor: isPro ? theme.primary : theme.warning }]}>
          <Text style={styles.pillText}>{isPro ? "PRO UNLIMITED" : "FREE TIER"}</Text>
        </View>
        <Text style={[styles.label, { color: theme.text }]}>
          {isPro ? "Full Autonomous Capabilities" : `${remaining} of ${MAX_DAILY_FREE_APPLICATIONS} daily applications remaining`}
        </Text>
      </View>
      {!isPro && onUpgradePress && (
        <Pressable
          accessibilityRole="button"
          onPress={onUpgradePress}
          style={({ pressed }) => [styles.upgradeBtn, { backgroundColor: theme.primary, opacity: pressed ? 0.8 : 1 }]}
        >
          <Text style={styles.upgradeText}>Upgrade</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    borderWidth: 1,
    marginVertical: 4,
  },
  left: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    flex: 1,
  },
  pill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  pillText: {
    color: "#fff",
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.5,
  },
  label: {
    fontSize: 12,
    fontWeight: "600",
    flex: 1,
  },
  upgradeBtn: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 8,
  },
  upgradeText: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "700",
  },
});
