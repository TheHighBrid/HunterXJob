import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/theme";

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.surface, borderColor: theme.border },
      ]}
    >
      <Text style={[styles.value, { color: theme.text }]}>{value}</Text>
      <Text style={[styles.label, { color: theme.textMuted }]}>{label}</Text>
      {hint ? <Text style={[styles.hint, { color: theme.textFaint }]}>{hint}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexBasis: "47%",
    flexGrow: 1,
    borderRadius: 14,
    borderWidth: 1,
    paddingVertical: 16,
    paddingHorizontal: 14,
    gap: 4,
  },
  value: {
    fontSize: 26,
    fontWeight: "800",
  },
  label: {
    fontSize: 13,
    fontWeight: "600",
  },
  hint: {
    fontSize: 11,
    marginTop: 2,
  },
});
