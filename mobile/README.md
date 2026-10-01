# HunterXJob mobile

A phone remote control for the **HunterXJob v2 server** (`../v2`). It shows
status, the kill switch, the scheduler, jobs, the review queue, reports and
settings. It's built with Expo (SDK 57, React Native, TypeScript, strict mode)
and Expo Router.

The app does no automation itself. All the work runs on the v2 server. It
doesn't need, or talk to, the legacy v1 `backend/`. Live submission is locked
on the server, and nothing in the app can unlock it. See `../docs/ARCHITECTURE.md`.

## Screens

| Tab | What it shows |
|---|---|
| **Dashboard** | Server status, live-submission lock, kill switch (engage anytime; disengage asks you to confirm), scheduler state with pause/resume/run-now, today's caps, recent cycles |
| **Jobs** | Searchable list with stage filter and scores. Detail shows the score breakdown, form status (fields planned, needing review, blockers), a read-only live form check, review tasks and the timeline |
| **Review** | Open/closed queue. Detail lists the reason codes and what approving would do. Approve, reject, resolve or dismiss, each with a confirmation. Approval never submits; at most it queues another dry-run |
| **Reports** | Pipeline counts, dry-runs, review backlog, cycles in the last 24h, 7-day history |
| **Settings** | Read-only safety section, the editable safe subset (caps, score threshold, quiet hours, cycle limits, targeting), AI info, backups list |
| **Connection** (modal, header button) | Server URL, API key, connection test |

## Setup

```bash
cd mobile
npm ci
npx expo start        # Expo Go / emulator; `w` opens a web preview (best effort)
```

The package manager is npm (`package-lock.json` is committed).

### Connecting to your server

On first launch the app opens **Connection**:

- **Server URL**: there is no built-in default; the field shows the placeholder
  `http://<your-vm>.<tailnet>.ts.net:8011`. Use the address from
  `../docs/DEPLOY_VM.md` §5:
  - `https://<vm>.<tailnet>.ts.net` with `tailscale serve`, or
  - `http://<vm>.<tailnet>.ts.net:8011` when v2 is bound to the VM's Tailscale IP.

  On the Android emulator, `http://10.0.2.2:8011` reaches a server on your computer.
- **API key**: `API_KEY` from `v2/.env` (at least 32 characters). It is stored with
  **expo-secure-store** (Android Keystore / iOS Keychain), never in AsyncStorage
  or app state on disk. Only the URL is kept in AsyncStorage. In the web
  preview, which has no secure store, the key is kept in `sessionStorage` for the current tab only.
- **Test connection** runs four steps: URL is valid → server reachable
  (`/api/health`) → key accepted (`/api/auth/check`) → server version ≥
  `MIN_SERVER_VERSION` (0.4.0). It warns if you use plain `http://` to a public address.

**Cleartext HTTP:** `app.json` enables `usesCleartextTraffic` on Android
(through `expo-build-properties`) so `http://…ts.net:8011` works. Tailscale
encrypts that traffic. For anything outside a tailnet, use `https://`.

The **web preview** calls the API from a browser, so the server must allow
its origin: set `CORS_ORIGINS=http://localhost:8081` in `v2/.env`.

### Errors

Requests time out after 15 s. Network, 401, 409 and 428 responses (for
example "cycle already running" or "confirm required") are shown as real
messages with Retry. The app never falls back to demo data.

## Typed API client

`src/api/schema.d.ts` is **generated** from the server's OpenAPI snapshot
(`../v2/openapi.json`) with `openapi-typescript`. `src/api/types.ts` gives
friendly names to those schemas, and `src/api/client.ts` is the only place that calls `fetch`.

When the v2 API changes:

```bash
cd ../v2 && ./hunterx openapi      # refresh v2/openapi.json
cd ../mobile && npm run api:generate
```

CI runs `npm run api:check`, which fails if `schema.d.ts` is stale.
`src/__tests__/apiContract.test.ts` checks that every method and path the client calls exists in the snapshot.

## Checks

```bash
npm run typecheck     # tsc --noEmit
npm test              # jest-expo unit tests
npm run api:check     # generated types match ../v2/openapi.json
npx expo export --platform android   # bundle sanity check (output in dist/, gitignored)
```

`../scripts/test-all.sh` and the `mobile` CI job run the first three.

## Project layout

```
app/                       Expo Router routes
  _layout.tsx              root stack (tabs + Connection modal)
  connection.tsx           server URL, API key, connection test
  (tabs)/_layout.tsx       tab bar; redirects to Connection until configured
  (tabs)/index.tsx         Dashboard
  (tabs)/jobs/             Jobs list + [id] detail
  (tabs)/review/           Review queue + [id] detail with actions
  (tabs)/reports.tsx       Reports
  (tabs)/settings.tsx      Settings
src/
  api/                     generated schema, type aliases, client, connection test
  lib/                     URL rules, secure key storage, confirm dialogs
  store/connection.ts      persisted server URL (zustand + AsyncStorage)
  safety.ts                kill-switch request rules and safety copy
  settingsForm.ts          settings form <-> PATCH body (changed, editable fields only)
  hooks/useApiResource.ts  load on focus, pull-to-refresh, optional polling
  components/              shared UI
  __tests__/               jest unit tests
```

## Building an Android APK

No EAS account is needed:

```bash
npx expo prebuild -p android
cd android && ./gradlew assembleDebug    # app/build/outputs/apk/debug/app-debug.apk
```

A signed release needs your own keystore (see Expo's "local builds" guide). The
applicationId comes from `app.json` (`com.hunterxjob.app`).
