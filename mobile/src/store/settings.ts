import AsyncStorage from "@react-native-async-storage/async-storage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Connection + locally-cached automation preferences, persisted to
 * AsyncStorage. `baseUrl` / `apiKey` are read by src/api/client.ts on every
 * request. `automationEnabled` / `dailyCap` are cached locally so the
 * Settings screen has something to show instantly (and something to fall
 * back to offline); the Settings screen is also responsible for pushing
 * them to the backend via PUT /api/settings when it can reach it.
 */
export interface SettingsState {
  baseUrl: string;
  apiKey: string;
  automationEnabled: boolean;
  dailyCap: number;
  /** True once AsyncStorage has been read and this store reflects saved state. */
  hasHydrated: boolean;
  /** True once the user has been through the Settings screen at least once. */
  hasCompletedSetup: boolean;
  setConnection: (baseUrl: string, apiKey: string) => void;
  setAutomationPrefs: (automationEnabled: boolean, dailyCap: number) => void;
  markSetupComplete: () => void;
  setHasHydrated: (value: boolean) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      baseUrl: "",
      apiKey: "",
      automationEnabled: false,
      dailyCap: 15,
      hasHydrated: false,
      hasCompletedSetup: false,
      setConnection: (baseUrl, apiKey) => set({ baseUrl, apiKey }),
      setAutomationPrefs: (automationEnabled, dailyCap) =>
        set({ automationEnabled, dailyCap }),
      markSetupComplete: () => set({ hasCompletedSetup: true }),
      setHasHydrated: (value) => set({ hasHydrated: value }),
    }),
    {
      name: "hunterxjob-settings",
      storage: createJSONStorage(() => AsyncStorage),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);

/** Convenience helper for non-hook contexts (e.g. the API client). */
export function getConnectionConfig(): { baseUrl: string; apiKey: string } {
  const { baseUrl, apiKey } = useSettingsStore.getState();
  return { baseUrl: baseUrl.trim().replace(/\/+$/, ""), apiKey: apiKey.trim() };
}

export function isBackendConfigured(): boolean {
  return getConnectionConfig().baseUrl.length > 0;
}
