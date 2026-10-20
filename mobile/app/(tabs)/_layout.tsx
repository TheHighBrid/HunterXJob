import { Tabs, useRouter } from "expo-router";
import { useEffect, useRef } from "react";
import { Text, type ColorValue } from "react-native";

import { ConnectionButton } from "@/components/ConnectionButton";
import { isConfigured, useConnection } from "@/store/connection";
import { useTheme } from "@/theme";

function TabIcon({ symbol, focused, color }: { symbol: string; focused: boolean; color: ColorValue }) {
  return <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.6, color }}>{symbol}</Text>;
}

export default function TabsLayout() {
  const theme = useTheme();
  const router = useRouter();
  const ready = useConnection((s) => s.ready);
  const configured = useConnection(isConfigured);
  const redirected = useRef(false);

  // First run (no server URL or key yet): open the Connection screen once.
  useEffect(() => {
    if (!ready || redirected.current || configured) return;
    redirected.current = true;
    router.push("/connection");
  }, [ready, configured, router]);

  const header = {
    headerRight: () => <ConnectionButton />,
    headerStyle: { backgroundColor: theme.surface },
    headerTintColor: theme.text,
    headerShadowVisible: false,
  };

  return (
    <Tabs
      screenOptions={{
        ...header,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textFaint,
        tabBarStyle: { backgroundColor: theme.surface, borderTopColor: theme.border },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: "Dashboard", tabBarIcon: ({ focused, color }) => <TabIcon symbol="🏠" focused={focused} color={color} /> }}
      />
      <Tabs.Screen
        name="jobs"
        options={{ title: "Jobs", headerShown: false, tabBarIcon: ({ focused, color }) => <TabIcon symbol="🔍" focused={focused} color={color} /> }}
      />
      <Tabs.Screen
        name="review"
        options={{ title: "Review", headerShown: false, tabBarIcon: ({ focused, color }) => <TabIcon symbol="📝" focused={focused} color={color} /> }}
      />
      <Tabs.Screen
        name="reports"
        options={{ title: "Reports", tabBarIcon: ({ focused, color }) => <TabIcon symbol="📊" focused={focused} color={color} /> }}
      />
      <Tabs.Screen
        name="settings"
        options={{ title: "Settings", tabBarIcon: ({ focused, color }) => <TabIcon symbol="⚙️" focused={focused} color={color} /> }}
      />
    </Tabs>
  );
}
