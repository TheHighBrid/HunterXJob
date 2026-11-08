import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/theme";

export function StatCard({
  label,
  value,
  hint,
  accentColor,
  onPress,
}: {
  label: string;
  value: string;
  hint?: string;
  accentColor?: string;
  onPress?: () => void;
}) {
  const theme = useTheme();
  const content = (
    <View
      style={[
        styles.card,
        {
          backgroundColor: theme.surface,
          borderColor: accentColor ? accentColor + "55" : theme.border,
          borderTopColor: accentColor ?? theme.border,
          borderTopWidth: accentColor ? 3 : 1,
        },
      ]}
    >
      <Text style={[styles.value, { color: accentColor ?? theme.text }]}>{value}</Text>
      <Text style={[styles.label, { color: theme.text }]}>{label}</Text>
      {hint ? <Text style={[styles.hint, { color: theme.textMuted }]}>{hint}</Text> : null}
    </View>
  );
  if (onPress) {
    return (
      <Pressable onPress={onPress} style={({ pressed }) => [styles.pressWrap, { opacity: pressed ? 0.85 : 1 }]}>
        {content}
      </Pressable>
    );
  }
  return content;
}

const styles = StyleSheet.create({
  pressWrap: {
    flexBasis: "47%",
    flexGrow: 1,
  },
  card: {
    flexBasis: "47%",
    flexGrow: 1,
    borderRadius: 16,
    borderWidth: 1,
    paddingVertical: 16,
    paddingHorizontal: 14,
    gap: 4,
  },
  value: {
    fontSize: 28,
    fontWeight: "900",
    letterSpacing: -0.5,
  },
  label: {
    fontSize: 13,
    fontWeight: "700",
  },
  hint: {
    fontSize: 11,
    marginTop: 2,
    fontWeight: "500",
  },
});
