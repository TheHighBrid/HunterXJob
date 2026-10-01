import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";

/**
 * The API key lives in the OS keystore (Android Keystore / iOS Keychain) via
 * expo-secure-store, never in AsyncStorage. The web preview has no secure
 * store, so there it is kept in sessionStorage (cleared when the tab closes)
 * and is meant for local development only.
 */
const KEY_NAME = "hunterxjob.apiKey";

function webStorage(): Storage | null {
  return typeof sessionStorage === "undefined" ? null : sessionStorage;
}

export async function loadApiKey(): Promise<string> {
  if (Platform.OS === "web") return webStorage()?.getItem(KEY_NAME) ?? "";
  return (await SecureStore.getItemAsync(KEY_NAME)) ?? "";
}

export async function saveApiKey(value: string): Promise<void> {
  if (Platform.OS === "web") {
    webStorage()?.setItem(KEY_NAME, value);
    return;
  }
  await SecureStore.setItemAsync(KEY_NAME, value, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export async function clearApiKey(): Promise<void> {
  if (Platform.OS === "web") {
    webStorage()?.removeItem(KEY_NAME);
    return;
  }
  await SecureStore.deleteItemAsync(KEY_NAME);
}
