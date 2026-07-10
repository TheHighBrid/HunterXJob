import { create } from "zustand";

import type { ApplicationRecord, JobPosting, Report } from "@/types";

/**
 * Lightweight in-memory cache of the last successful list fetches.
 *
 * The documented API surface (docs/ARCHITECTURE.md section 5) only lists
 * collection endpoints (GET /api/jobs, GET /api/applications, GET
 * /api/reports) — no single-item GET by id. Detail screens are reached by
 * tapping a row in an already-fetched list, so we stash the last list here
 * and look items up by id, instead of inventing GET /api/jobs/{id} etc.
 * that the backend may not implement. If a detail screen is opened without
 * a warm cache (e.g. a deep link), it falls back to re-fetching the list.
 */
interface DataCacheState {
  jobs: JobPosting[];
  applications: ApplicationRecord[];
  reports: Report[];
  isDemoData: boolean;
  setJobs: (jobs: JobPosting[], isDemoData: boolean) => void;
  setApplications: (applications: ApplicationRecord[], isDemoData: boolean) => void;
  setReports: (reports: Report[], isDemoData: boolean) => void;
  upsertApplication: (application: ApplicationRecord) => void;
}

export const useDataCacheStore = create<DataCacheState>()((set, get) => ({
  jobs: [],
  applications: [],
  reports: [],
  isDemoData: false,
  setJobs: (jobs, isDemoData) => set({ jobs, isDemoData }),
  setApplications: (applications, isDemoData) => set({ applications, isDemoData }),
  setReports: (reports, isDemoData) => set({ reports, isDemoData }),
  upsertApplication: (application) => {
    const existing = get().applications;
    const index = existing.findIndex((a) => a.id === application.id);
    if (index === -1) {
      set({ applications: [application, ...existing] });
    } else {
      const next = existing.slice();
      next[index] = application;
      set({ applications: next });
    }
  },
}));
