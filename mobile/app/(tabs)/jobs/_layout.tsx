import { Stack } from "expo-router";

import { SettingsButton } from "@/components/SettingsButton";
import { useTheme } from "@/theme";

export default function JobsStackLayout() {
  const theme = useTheme();
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.surface },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ title: "Job Feed", headerRight: () => <SettingsButton /> }} />
      <Stack.Screen name="[id]" options={{ title: "Job Details" }} />
    </Stack>
  );
}
