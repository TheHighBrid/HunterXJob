import { Tabs, useRouter } from "expo-router";
import { useEffect, useRef } from "react";
import { Text, type ColorValue } from "react-native";

import { SettingsButton } from "@/components/SettingsButton";
import { useSettingsStore } from "@/store/settings";
import { useTheme } from "@/theme";

function TabIcon({ symbol, focused, color }: { symbol: string; focused: boolean; color: ColorValue }) {
  return (
    <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.6, color }}>{symbol}</Text>
  );
}

export default function TabsLayout() {
  const theme = useTheme();
  const router = useRouter();
  const hasHydrated = useSettingsStore((s) => s.hasHydrated);
  const baseUrl = useSettingsStore((s) => s.baseUrl);
  const hasCompletedSetup = useSettingsStore((s) => s.hasCompletedSetup);
  const redirected = useRef(false);

  // First-run: send the user to Settings before they hit a wall of API
  // errors on every tab. Only happens once per app launch.
  useEffect(() => {
    if (!hasHydrated || redirected.current) return;
    if (!baseUrl && !hasCompletedSetup) {
      redirected.current = true;
      router.push("/settings");
    }
  }, [hasHydrated, baseUrl, hasCompletedSetup, router]);

  return (
    <Tabs
      screenOptions={{
        headerRight: () => <SettingsButton />,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textFaint,
        tabBarStyle: {
          backgroundColor: theme.surface,
          borderTopColor: theme.border,
        },
        headerStyle: { backgroundColor: theme.surface },
        headerTintColor: theme.text,
        headerShadowVisible: false,
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Dashboard",
          tabBarIcon: ({ focused, color }) => <TabIcon symbol="🏠" focused={focused} color={color} />,
        }}
      />
      <Tabs.Screen
        name="jobs"
        options={{
          title: "Jobs",
          headerShown: false,
          tabBarIcon: ({ focused, color }) => <TabIcon symbol="🔍" focused={focused} color={color} />,
        }}
      />
      <Tabs.Screen
        name="applications"
        options={{
          title: "Applications",
          headerShown: false,
          tabBarIcon: ({ focused, color }) => <TabIcon symbol="📋" focused={focused} color={color} />,
        }}
      />
      <Tabs.Screen
        name="reports"
        options={{
          title: "Reports",
          headerShown: false,
          tabBarIcon: ({ focused, color }) => <TabIcon symbol="📊" focused={focused} color={color} />,
        }}
      />
    </Tabs>
  );
}
