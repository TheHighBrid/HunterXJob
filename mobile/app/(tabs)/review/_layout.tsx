import { Stack } from "expo-router";

import { ConnectionButton } from "@/components/ConnectionButton";
import { useTheme } from "@/theme";

export default function ReviewLayout() {
  const theme = useTheme();
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.surface },
        headerTintColor: theme.text,
        headerShadowVisible: false,
        headerRight: () => <ConnectionButton />,
      }}
    >
      <Stack.Screen name="index" options={{ title: "Review queue" }} />
      <Stack.Screen name="[id]" options={{ title: "Review task" }} />
    </Stack>
  );
}
