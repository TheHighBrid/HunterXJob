import AsyncStorage from "@react-native-async-storage/async-storage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { clearApiKey, loadApiKey, saveApiKey } from "@/lib/secureKey";

/**
 * Where the v2 server is and how to authenticate. The URL is not secret and is
 * persisted in AsyncStorage; the API key is kept in memory and in the secure
 * store only (see src/lib/secureKey.ts).
 */
export interface ConnectionState {
  baseUrl: string;
  apiKey: string;
  /** AsyncStorage and the secure store have both been read. */
  ready: boolean;
  setConnection: (baseUrl: string, apiKey: string) => Promise<void>;
  forget: () => Promise<void>;
  hydrateKey: () => Promise<void>;
}

export const useConnection = create<ConnectionState>()(
  persist(
    (set) => ({
      baseUrl: "",
      apiKey: "",
      ready: false,
      setConnection: async (baseUrl, apiKey) => {
        await saveApiKey(apiKey);
        set({ baseUrl, apiKey });
      },
      forget: async () => {
        await clearApiKey();
        set({ baseUrl: "", apiKey: "" });
      },
      hydrateKey: async () => {
        let apiKey = "";
        try {
          apiKey = await loadApiKey();
        } finally {
          set({ apiKey, ready: true });
        }
      },
    }),
    {
      name: "hunterxjob-connection",
      storage: createJSONStorage(() => AsyncStorage),
      // Only the URL is persisted here. Never add apiKey to this list.
      partialize: (state) => ({ baseUrl: state.baseUrl }),
      onRehydrateStorage: () => (state) => {
        void state?.hydrateKey();
      },
    }
  )
);

export function getConnection(): { baseUrl: string; apiKey: string } {
  const { baseUrl, apiKey } = useConnection.getState();
  return { baseUrl, apiKey };
}

export function isConfigured(state: Pick<ConnectionState, "baseUrl" | "apiKey">): boolean {
  return state.baseUrl.length > 0 && state.apiKey.length > 0;
}
