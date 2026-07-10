import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "@/theme";

/**
 * Shown whenever a screen is rendering bundled mock data instead of live
 * API data, so it's never mistaken for real backend state.
 */
export function DemoDataBanner({ reason }: { reason?: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.banner, { backgroundColor: theme.warning + "26", borderColor: theme.warning }]}>
      <Text style={[styles.text, { color: theme.text }]}>
        Offline / demo data — {reason ?? "backend not reachable"}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    marginHorizontal: 16,
    marginTop: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
  },
  text: {
    fontSize: 13,
    fontWeight: "600",
  },
});
