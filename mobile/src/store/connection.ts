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
  setConnection: typeof setConnection;
  forget: typeof forgetConnection;
  hydrateKey: typeof hydrateKey;
}

export const useConnection = create<ConnectionState>()(
  persist(
    (): ConnectionState => ({
      baseUrl: "",
      apiKey: "",
      ready: false,
      setConnection,
      forget: forgetConnection,
      hydrateKey,
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

/** Save a tested connection: the key goes to the secure store, the URL to AsyncStorage. */
async function setConnection(baseUrl: string, apiKey: string): Promise<void> {
  await saveApiKey(apiKey);
  useConnection.setState({ baseUrl, apiKey });
}

async function forgetConnection(): Promise<void> {
  await clearApiKey();
  useConnection.setState({ baseUrl: "", apiKey: "" });
}

async function hydrateKey(): Promise<void> {
  let apiKey = "";
  try {
    apiKey = await loadApiKey();
  } finally {
    useConnection.setState({ apiKey, ready: true });
  }
}

export function getConnection(): { baseUrl: string; apiKey: string } {
  const { baseUrl, apiKey } = useConnection.getState();
  return { baseUrl, apiKey };
}

export function isConfigured(state: Pick<ConnectionState, "baseUrl" | "apiKey">): boolean {
  return state.baseUrl.length > 0 && state.apiKey.length > 0;
}
