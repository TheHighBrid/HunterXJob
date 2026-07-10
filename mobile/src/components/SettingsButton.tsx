import { useRouter } from "expo-router";
import { Pressable, Text } from "react-native";

import { useTheme } from "@/theme";

export function SettingsButton() {
  const router = useRouter();
  const theme = useTheme();
  return (
    <Pressable
      accessibilityLabel="Open settings"
      hitSlop={12}
      onPress={() => router.push("/settings")}
      style={{ paddingHorizontal: 12, paddingVertical: 6 }}
    >
      <Text style={{ fontSize: 20, color: theme.text }}>⚙️</Text>
    </Pressable>
  );
}
