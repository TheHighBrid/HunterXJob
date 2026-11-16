---
name: hunterx-apk-compilation
description: >-
  Build, sign, verify, and stage Android APK packages for HunterXJob. Use whenever compiling
  a new Android release APK, packaging mobile distribution artifacts, or running Android release builds.
---

# HunterXJob Android APK Compilation Procedure

This skill provides the authoritative build and verification runbook for generating production Android APK packages (`com.hunterxjob.app`) for HunterXJob.

## 1. Environment & Toolchain Setup
On macOS, ensure OpenJDK 17 and Android SDK 34 are exported in the environment:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/Users/tyf12/Library/Android/sdk
export PATH=$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH
```

Verify that Java and Android build tools resolve correctly:
```bash
java -version
$ANDROID_HOME/build-tools/36.0.0/apksigner --version
```

## 2. Pre-Build Code Quality & Type Checks
Run all client-side validation checks from the `mobile/` directory:

```bash
cd mobile

# 1. Verify TypeScript types
npm run typecheck

# 2. Run Jest test suite (subscription tiers, API contracts, store)
npm test

# 3. Verify client schema against v2/openapi.json
npm run api:check

# 4. Verify Metro bundler Hermes export
npx expo export --platform android
```

## 3. Assemble Release APK
Build the release package using the Gradle wrapper and production release keystore (`mobile/android/app/release.keystore`):

```bash
cd mobile/android
./gradlew assembleRelease --no-daemon
```

*Output file:* `mobile/android/app/build/outputs/apk/release/app-release.apk`

## 4. Signature & Checksum Verification
Verify the APK signature using Android `apksigner` (must pass Scheme v2 with production keystore):

```bash
$ANDROID_HOME/build-tools/36.0.0/apksigner verify --verbose mobile/android/app/build/outputs/apk/release/app-release.apk
```

## 5. Stage Release Artifacts
Stage the compiled APK into `releases/` and update checksum and documentation:

```bash
# Copy to releases/
cp mobile/android/app/build/outputs/apk/release/app-release.apk releases/HunterXJob-v1.0.0.apk

# Generate SHA-256 digest
cd releases
shasum -a 256 HunterXJob-v1.0.0.apk > HunterXJob-v1.0.0.apk.sha256

# Verify integrity
shasum -a 256 -c HunterXJob-v1.0.0.apk.sha256
```

Update the checksum entry in [`releases/RELEASE_v1.0.0.md`](file:///releases/RELEASE_v1.0.0.md) with the generated SHA-256 hash.
