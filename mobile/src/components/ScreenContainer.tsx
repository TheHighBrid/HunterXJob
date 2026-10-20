import type { PropsWithChildren } from "react";
import { StyleSheet, View } from "react-native";

import { useTheme } from "@/theme";

/** Full-bleed background wrapper so every screen agrees on the theme background. */
export function ScreenContainer({ children }: PropsWithChildren) {
  const theme = useTheme();
  return (
    <View style={[styles.container, { backgroundColor: theme.background }]}>{children}</View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
});
