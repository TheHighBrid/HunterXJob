import { Alert, Platform } from "react-native";

/** Cross-platform yes/no prompt (Alert on native, window.confirm on web). */
export function confirmAsync(title: string, message: string, confirmLabel = "OK", destructive = false): Promise<boolean> {
  if (Platform.OS === "web") {
    const ok = typeof window !== "undefined" && typeof window.confirm === "function" ? window.confirm(`${title}\n\n${message}`) : false;
    return Promise.resolve(ok);
  }
  return new Promise((resolve) => {
    Alert.alert(title, message, [
      { text: "Cancel", style: "cancel", onPress: () => { resolve(false); } },
      { text: confirmLabel, style: destructive ? "destructive" : "default", onPress: () => { resolve(true); } },
    ], { cancelable: true, onDismiss: () => { resolve(false); } });
  });
}

export function notify(title: string, message: string): void {
  if (Platform.OS === "web") {
    if (typeof window !== "undefined" && typeof window.alert === "function") window.alert(`${title}\n\n${message}`);
    return;
  }
  Alert.alert(title, message);
}
