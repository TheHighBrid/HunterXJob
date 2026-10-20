import { useRouter } from "expo-router";
import { Pressable, Text } from "react-native";

import { useTheme } from "@/theme";

export function CloseButton() {
  const router = useRouter();
  const theme = useTheme();
  return (
    <Pressable
      accessibilityLabel="Close"
      hitSlop={12}
      onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
      style={{ paddingHorizontal: 12, paddingVertical: 6 }}
    >
      <Text style={{ fontSize: 15, color: theme.primary, fontWeight: "600" }}>Done</Text>
    </Pressable>
  );
}
