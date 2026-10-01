import type { PropsWithChildren, ReactNode } from "react";
import { StyleSheet, Text, View, type ViewStyle } from "react-native";

import { useTheme } from "@/theme";

export function Card({ title, right, children, style }: PropsWithChildren<{ title?: string; right?: ReactNode; style?: ViewStyle }>) {
  const theme = useTheme();
  const hasHeader = (title !== undefined && title !== "") || (right !== undefined && right !== null);
  return (
    <View style={[styles.card, { backgroundColor: theme.surface, borderColor: theme.border }, style]}>
      {hasHeader ? (
        <View style={styles.header}>
          {title ? <Text style={[styles.title, { color: theme.textMuted }]}>{title}</Text> : <View />}
          {right}
        </View>
      ) : null}
      {children}
    </View>
  );
}

export function Row({ label, value, valueColor, mono }: { label: string; value: ReactNode; valueColor?: string; mono?: boolean }) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <Text style={[styles.rowLabel, { color: theme.textMuted }]}>{label}</Text>
      {typeof value === "string" || typeof value === "number" ? (
        <Text
          style={[styles.rowValue, { color: valueColor ?? theme.text }, mono ? styles.mono : null]}
          numberOfLines={2}
        >
          {value}
        </Text>
      ) : (
        value
      )}
    </View>
  );
}

export function Muted({ children }: PropsWithChildren) {
  const theme = useTheme();
  return <Text style={[styles.muted, { color: theme.textMuted }]}>{children}</Text>;
}

const styles = StyleSheet.create({
  card: { borderRadius: 14, borderWidth: 1, padding: 14, gap: 10 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: 12, fontWeight: "800", letterSpacing: 0.6, textTransform: "uppercase" },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  rowLabel: { fontSize: 14, flexShrink: 0 },
  rowValue: { fontSize: 14, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  mono: { fontFamily: "monospace", fontSize: 12 },
  muted: { fontSize: 13, lineHeight: 19 },
});
