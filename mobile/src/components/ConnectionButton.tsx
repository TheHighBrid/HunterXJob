import { useRouter } from "expo-router";
import { Pressable, Text } from "react-native";

import { useTheme } from "@/theme";

/** Header button that opens the Connection screen. */
export function ConnectionButton() {
  const router = useRouter();
  const theme = useTheme();
  return (
    <Pressable
      accessibilityLabel="Open connection settings"
      hitSlop={12}
      onPress={() => router.push("/connection")}
      style={{ paddingHorizontal: 12, paddingVertical: 6 }}
    >
      <Text style={{ fontSize: 18, color: theme.text }}>🔌</Text>
    </Pressable>
  );
}
