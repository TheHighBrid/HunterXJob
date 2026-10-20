import { Stack } from "expo-router";
import { useColorScheme } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";

import { CloseButton } from "@/components/CloseButton";
import { useTheme } from "@/theme";

export default function RootLayout() {
  const scheme = useColorScheme();
  const theme = useTheme();
  const pushed = {
    headerShown: true,
    headerStyle: { backgroundColor: theme.surface },
    headerTintColor: theme.text,
    headerShadowVisible: false,
  };

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar style={scheme === "dark" ? "light" : "dark"} />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen
            name="connection"
            options={{
              presentation: "modal",
              headerShown: true,
              title: "Connection",
              headerRight: () => <CloseButton />,
              headerStyle: { backgroundColor: theme.surface },
              headerTintColor: theme.text,
            }}
          />
          <Stack.Screen name="profile" options={{ ...pushed, title: "Profile" }} />
          <Stack.Screen name="materials/[id]" options={{ ...pushed, title: "Materials" }} />
        </Stack>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
