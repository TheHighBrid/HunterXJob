import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/theme";

export function Banner({ tone, message }: { tone: "danger" | "warning" | "info"; message: string }) {
  const theme = useTheme();
  const color = tone === "danger" ? theme.danger : tone === "warning" ? theme.warning : theme.info;
  return (
    <View style={[styles.banner, { borderColor: color, backgroundColor: color + "1A" }]}>
      <Text style={[styles.text, { color }]}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { borderWidth: 1, borderRadius: 12, padding: 10 },
  text: { fontSize: 13, fontWeight: "600", lineHeight: 18 },
});
