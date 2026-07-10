# HunterXJob Mobile

A control panel for the HunterXJob backend: job feed, application tracker, reports,
and automation settings. Built with Expo (React Native + TypeScript) and Expo Router.

The backend it talks to (Python/FastAPI, in `../backend/`) is being built separately.
See `../docs/ARCHITECTURE.md` for the overall system design.

## Stack

- **Expo** (managed workflow, SDK 57) + **Expo Router** (file-based navigation:
  a bottom tab bar with nested stacks per tab, and Settings as a modal).
- **TypeScript**, strict mode.
- **Zustand** (+ `@react-native-async-storage/async-storage`) for persisted
  connection settings.
- **Package manager: npm.** Use `npm install` / `npm run <script>` — a
  `package-lock.json` is committed, so stick with npm rather than switching to
  yarn/pnpm to avoid a second lockfile.

No native modules beyond what Expo's managed workflow + `expo prebuild` handle
out of the box (async-storage, gesture-handler, safe-area-context, screens —
all standard Expo Router dependencies). No EAS account is required; this is
built to be prebuilt and compiled locally.

## Project layout

```
mobile/
  app/                        Expo Router routes
    _layout.tsx                Root stack (tabs + settings modal)
    settings.tsx                Settings screen (modal)
    (tabs)/
      _layout.tsx               Tab bar (Dashboard / Jobs / Applications / Reports)
      index.tsx                 Dashboard
      jobs/_layout.tsx           Stack for the Jobs tab
      jobs/index.tsx              Job Feed (list)
      jobs/[id].tsx                Job detail
      applications/_layout.tsx   Stack for the Applications tab
      applications/index.tsx      Applications (grouped/filterable list)
      applications/[id].tsx        Application detail (status/notes edit)
      reports/_layout.tsx        Stack for the Reports tab
      reports/index.tsx           Reports (list)
      reports/[id].tsx             Report detail
  src/
    api/client.ts               Typed fetch wrapper (timeouts, error states)
    store/settings.ts           AsyncStorage-backed connection settings (Zustand)
    store/dataCache.ts          In-memory cache of last-fetched lists, for detail screens
    types.ts                    Types mirroring the backend's Pydantic schemas
    theme.ts                    Light/dark color tokens + status colors
    components/                 Shared UI (StatCard, Badge, buttons, banners, states)
    hooks/useApiResource.ts     Fetch + pull-to-refresh + demo-data-fallback hook
    mock/data.ts                Bundled offline/demo data
    utils/format.ts             Date/label formatting helpers
```

## Setup

```bash
cd mobile
npm install
```

## Running in development

```bash
npx expo start
```

Scan the QR code with **Expo Go** (Android/iOS) for the fastest loop, or press
`a` / `i` in the terminal to launch an Android/iOS emulator if you have one
configured. Press `w` for a web preview (best-effort; the app is designed and
tested primarily for native).

### Pointing it at your backend

On first launch the app opens the **Settings** screen automatically (it's also
reachable any time via the gear icon in the header). Enter:

- **Backend base URL** — e.g. `http://192.168.1.20:8000` if you're running the
  backend on your LAN and testing on a physical device (`localhost` will not
  resolve to your dev machine from a phone/emulator — use your machine's LAN
  IP, or `http://10.0.2.2:8000` for the Android Studio emulator).
- **API key** — the value of `API_KEY` from the backend's `.env` (see
  `backend/app/config.py`), sent as the `X-API-Key` header on every request.

Tap **Save**. The app persists these to `AsyncStorage` and every screen's API
calls immediately start using them.

If you don't have a backend running yet, tap **Continue with offline/demo
data** on the Settings screen — every screen falls back to bundled sample
data and shows an explicit "Offline / demo data" banner so it's never
mistaken for live data. Once you do configure a reachable backend, the demo
banners disappear automatically.

### Error handling behavior

- Requests time out after 12s and surface a clear message (never an infinite
  spinner).
- Connectivity failures (unset backend URL, network error, timeout) fall back
  to demo data with a visible banner + the underlying error message, so you
  can debug your backend URL without the whole app going blank.
- HTTP-level failures (401 bad API key, 500s, etc.) are shown as real error
  states with a **Retry** button instead of being silently replaced by demo
  data — that would hide a real misconfiguration.

## Type checking

```bash
npx tsc --noEmit
```

## Sanity-checking the bundle

```bash
npx expo export
```

This bundles the app for iOS + Android without needing a device, emulator, or
EAS account — it's the fastest way to confirm the app actually builds. Output
goes to `dist/` (gitignored).

## Building an Android APK later

This repo does not run `expo prebuild` or Gradle as part of this task — that's
a separate step (needs the Android SDK / NDK / Gradle toolchain). When you're
ready:

```bash
cd mobile
npx expo prebuild -p android      # generates the native android/ project
cd android
./gradlew assembleDebug           # produces app/build/outputs/apk/debug/app-debug.apk
```

For a signed release build you'll need your own keystore; see Expo's
["Create a build without EAS"](https://docs.expo.dev/build-reference/local-builds/)
and `android/app/build.gradle`'s `signingConfigs` once the native project has
been generated. `expo prebuild` reads `app.json`'s `android.package`
(`com.hunterxjob.app`) to set the applicationId.

## API contract

This client was originally built against assumed API shapes (the backend was
still scaffolding when this app was first written) and has since been
reconciled against the real backend — see `backend/app/schemas.py` and
`backend/app/routers/`. Notable points, in case the two drift again:

- **IDs are UUID strings**, not numbers (SQLAlchemy `String` primary keys
  with a `uuid4` default). Every `id` / `*_id` field in `src/types.ts` is
  `string`.
- `GET /api/applications` does **not** embed job info — only
  `job_posting_id`. The optional `job?: JobPosting` field on
  `ApplicationRecord` is populated client-side from the jobs cache
  (`src/store/dataCache.ts`), not by the backend.
- There is no single-item `GET /api/jobs/{id}` or `GET /api/reports/{id}`.
  Detail screens look the item up in the last-fetched list
  (`src/store/dataCache.ts`), falling back to a full re-fetch for deep links.
  `GET /api/applications/{id}` *does* exist, but isn't currently used by the
  detail screen since the list is already cached.
- `Report.summary` is a parsed object (backend's `ReportOut.summary` —
  `Report.summary_json` is parsed server-side via a model property), shaped
  as `{ jobs_discovered_today, applications_submitted_today,
  applications_submitted_this_week, pending_review_count,
  applications_blocked, highlights[], errors[] }`.
- `GET /api/settings` / `PUT /api/settings` exchange an `AutomationSettings`
  object with `automation_enabled`, `max_applications_per_day`,
  `min_delay_between_applications_seconds`, `automation_dry_run` (not
  `dry_run`), `blacklisted_companies`, and read-only `llm_provider` /
  `llm_model`. `automation_enabled` is a real, enforced setting — when off,
  `automation_cycle` skips queued applications entirely (see
  `backend/app/services/safety.py::can_submit_now`).
- `GET /api/health` is unauthenticated and additionally reports
  `scheduler_running`, `next_scheduled_run` (ISO datetime, nullable),
  `llm_provider`, `llm_model` — used by the Dashboard's "Automation" section.

All API calls are centralized in `src/api/client.ts`; screens consume the
typed shapes from `src/types.ts`, not raw JSON.

## Deviations from the task spec

- **Navigation**: chose Expo Router (file-based) over React Navigation
  directly — it's the current Expo-recommended default, ships with typed
  routes, and needs no extra native linking beyond what's already pulled in
  (`react-native-screens`, `react-native-safe-area-context`,
  `react-native-gesture-handler`).
- **Icons**: tab bar and header icons use plain Unicode/emoji glyphs instead
  of an icon font library (e.g. `@expo/vector-icons`), to keep the dependency
  list minimal as instructed. Swap in a proper icon set later if desired —
  `app/(tabs)/_layout.tsx`'s `TabIcon` and `src/components/SettingsButton.tsx`
  / `CloseButton.tsx` are the only places that would need to change.
- Everything else follows the spec directly (five screens, shared
  `client.ts` / `settings.ts` / `types.ts`, tab bar + settings header icon,
  `app.json` identity, dark-mode awareness, offline/demo-data labeling).
