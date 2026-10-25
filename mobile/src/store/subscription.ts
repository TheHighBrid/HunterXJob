import AsyncStorage from "@react-native-async-storage/async-storage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type PlanTier = "free" | "pro";

export const MAX_DAILY_FREE_APPLICATIONS = 2;

export interface SubscriptionState {
  plan: PlanTier;
  licenseKey: string;
  dailyApplicationCount: number;
  lastApplicationDate: string;
  setPlan: (plan: PlanTier) => void;
  activatePro: (licenseKey: string) => boolean;
  recordApplication: () => boolean;
  canApply: () => boolean;
  canRunAutonomy: () => boolean;
  remainingToday: () => number;
}

function getTodayIsoDate(): string {
  return new Date().toISOString().split("T")[0];
}

export const useSubscription = create<SubscriptionState>()(
  persist(
    (set, get): SubscriptionState => ({
      plan: "free",
      licenseKey: "",
      dailyApplicationCount: 0,
      lastApplicationDate: getTodayIsoDate(),

      setPlan: (plan: PlanTier) => {
        set({ plan });
      },

      activatePro: (licenseKey: string) => {
        const cleaned = licenseKey.trim().toUpperCase();
        // Accepts any valid Pro key format: HUNTERX-PRO-*, HX-PRO-*, or PRO-PASS-*
        if (
          cleaned === "HUNTERX-PRO-2026" ||
          cleaned === "HUNTERX-PRO-UNLIMITED" ||
          cleaned.startsWith("HX-PRO-") ||
          cleaned.startsWith("HUNTERX-PRO-") ||
          cleaned.startsWith("PRO-")
        ) {
          set({ plan: "pro", licenseKey: cleaned });
          return true;
        }
        return false;
      },

      recordApplication: () => {
        const today = getTodayIsoDate();
        const current = get();
        let count = current.dailyApplicationCount;

        if (current.lastApplicationDate !== today) {
          count = 0;
        }

        if (current.plan === "free" && count >= MAX_DAILY_FREE_APPLICATIONS) {
          return false;
        }

        set({
          dailyApplicationCount: count + 1,
          lastApplicationDate: today,
        });
        return true;
      },

      canApply: () => {
        const current = get();
        if (current.plan === "pro") return true;

        const today = getTodayIsoDate();
        if (current.lastApplicationDate !== today) {
          return true;
        }
        return current.dailyApplicationCount < MAX_DAILY_FREE_APPLICATIONS;
      },

      canRunAutonomy: () => {
        return get().plan === "pro";
      },

      remainingToday: () => {
        const current = get();
        if (current.plan === "pro") return 9999;

        const today = getTodayIsoDate();
        const count = current.lastApplicationDate === today ? current.dailyApplicationCount : 0;
        return Math.max(0, MAX_DAILY_FREE_APPLICATIONS - count);
      },
    }),
    {
      name: "hunterxjob-subscription",
      storage: createJSONStorage(() => AsyncStorage),
    }
  )
);
