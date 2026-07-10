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

## API assumptions

`docs/ARCHITECTURE.md` section 5 documents the endpoint list at a high level,
but at the time this client was written `backend/app/routers/` and the
Pydantic response schemas didn't exist yet (only `backend/app/config.py`,
`db.py`, `auth.py` were scaffolded). To build a fully working client against
a not-yet-finalized API, this app makes the following concrete assumptions —
documented here so they're easy to reconcile once the backend schemas land:

- `GET /api/jobs` returns `JobPosting[]` (see `src/types.ts`) with fields
  matching the `job_posting` table described in ARCHITECTURE.md section 4.
- `GET /api/applications` returns `ApplicationRecord[]`, each optionally
  embedding its `job: JobPosting` (denormalized) so the list/detail screens
  don't need N follow-up requests. If the backend does not embed the job,
  the Applications screens fall back to showing `Job #<id>`.
- `POST /api/applications` accepts `{ job_posting_id, channel?, notes? }` and
  returns the created `ApplicationRecord` (used by the Job Feed's
  apply/queue button).
- `PATCH /api/applications/{id}` accepts a partial `{ status?, notes? }` and
  returns the updated `ApplicationRecord` (used by the Applications detail
  screen's status/notes editor). ARCHITECTURE.md only lists `GET/POST
  /api/applications` explicitly but describes the endpoint group as
  "list/create/update tracked applications" — PATCH-by-id is the natural
  read of "update."
- There is no documented single-item `GET /api/jobs/{id}` or `GET
  /api/reports/{id}`. Detail screens are reached by tapping a row in an
  already-fetched list, so the client caches the last list fetch
  (`src/store/dataCache.ts`) and looks up by id from there, re-fetching the
  full list as a fallback for deep links. If the backend does add single-item
  GET endpoints later, swapping the detail screens over is a small,
  contained change (`app/(tabs)/jobs/[id].tsx`,
  `app/(tabs)/reports/[id].tsx`).
- `GET /api/reports` / `GET /api/reports/latest` return `Report` objects
  shaped as `{ id, period, generated_at, summary }`, where `summary` carries
  the numbers the Dashboard needs (`jobs_discovered_today`,
  `applications_submitted_today`, `applications_submitted_this_week`,
  `pending_review_count`, `applications_blocked`, `highlights[]`, `errors[]`).
  This is a reasonable superset of the `report.summary_json` blob described
  in ARCHITECTURE.md section 4, inferred from what the Dashboard spec needs.
- `GET /api/health` additionally reports `next_scheduled_run` (ISO datetime,
  nullable) so the Dashboard can show "next scheduled run time" without a
  separate endpoint, plus `llm_provider` / `llm_model` / `scheduler_running`.
- `GET/PUT /api/settings` exchange an `AutomationSettings` object mirroring
  the safety-rail fields already defined in `backend/app/config.py`
  (`MAX_APPLICATIONS_PER_DAY`, `MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS`,
  `AUTOMATION_DRY_RUN`, `LLM_PROVIDER`, `BLACKLISTED_COMPANIES`), plus an
  `automation_enabled` boolean toggle (referenced by the mobile spec but not
  yet an explicit config field — treated as a `settings` key/value row per
  ARCHITECTURE.md section 4). `llm_provider` / `llm_model` are treated as
  backend-reported and read-only in the UI.

All API calls are centralized in `src/api/client.ts` — if the backend's real
field names differ, only `src/types.ts` and the mapping in `src/mock/data.ts`
should need updates; screens consume the typed shapes, not raw JSON.

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
