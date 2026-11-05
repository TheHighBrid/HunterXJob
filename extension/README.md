# HunterXJob Portable Autofill & Review Assistant ⚡

**HunterXJob Portable Extension** is a high-precision, portable Manifest V3 browser extension and in-page autofill companion designed to instantly fill job applications on **Indeed**, **LinkedIn (Easy Apply)**, **Canadian Public Sector & Government Portals (GC Jobs, Ontario Public Service, City of Ottawa, Gatineau, Canada Job Bank)**, and enterprise ATS platforms (**Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Workable, etc.**) with strict adherence to **verified candidate facts** and **pre-submission human review**.

---

## Key Features

1. **Portable Dual-Mode Operation**:
   - **Standalone Mode (Zero Setup)**: Operates immediately with embedded verified profile facts and 63+ production answer policies (`data/default_profile.json` and `data/default_vault.json`). No background servers required.
   - **Connected Mode**: Seamlessly syncs with your live local HunterXJob daemon (`http://localhost:8000`) to pull updated profile revisions and log submitted applications directly to the SQLite ledger.

2. **Foreground Autofill Mechanism**:
   - **Global Keyboard Shortcut**: Press `Alt + Shift + F` (or `Option + Shift + F` on macOS) from anywhere on the active tab to autofill the active foreground form or modal.
   - **In-Page Floating HUD Dock**: A sleek, draggable floating HUD that appears unobtrusively on job application pages with one-click **"Fill Form"**, live platform detection badge, and field counter.
   - **Extension Toolbar Popup**: Quick status check, candidate preview, and one-click application autofill.

3. **Truth Guard & Review-Before-Sending Guarantee**:
   - **Strictly Non-Destructive**: **Never auto-submits** forms. Leaves you in complete control to review every answer before sending.
   - **Visual Review Highlighting**: Filled fields receive a subtle glowing border and a review badge showing the source (e.g. `Verified Profile`, `Answer Vault: salary_expectation`, `Truth Guard`).
   - **In-Page Review Drawer**: Press `Alt + Shift + R` or click "Review" on the floating dock to inspect a checklist of every field filled and its value before submitting.

4. **Multi-Platform Intelligence**:
   - **LinkedIn**: Easy Apply multi-step modal (`.jobs-easy-apply-modal`), comboboxes, radio groups, and textareas.
   - **Indeed**: Smart Apply (`#ia-container`, `.ia-BasePage`), radio buttons, dropdowns, and screening questions.
   - **Canadian Government & Public Sector 🇨🇦**:
     - **GC Jobs** (`jobs.gc.ca`): Federal screening statements, SLE bilingual levels (Reading/Writing/Oral C/C/C), PSEA citizenship priority, security clearance validation (Reliability/Secret).
     - **Ontario Public Service (OPS)** (`gojobs.gov.on.ca`): Ontario provincial employment questions.
     - **City of Ottawa & Ville de Gatineau** (`ottawa.ca`, `gatineau.ca`, HRsmart, cvmanager): NCR municipal requirements and bilingual fluency.
     - **Canada Job Bank** (`jobbank.gc.ca`): National job board forms.
   - **Modern ATS Suites**: Full support for Workday (`data-automation-id`), Ashby (`[data-field-path]`), Greenhouse, Lever, SmartRecruiters, and generic web forms.
   - **SPA Framework Event Simulation**: Dispatches native prototype setters and synthetic `input`, `change`, `keydown`, `keyup`, and `blur` events so React, Angular, and Vue state listeners register values correctly.

---

## Quick Installation Guide

### Google Chrome, Brave, Microsoft Edge, Arc
1. Open your browser and navigate to the extensions page:
   - Chrome / Brave / Arc: `chrome://extensions`
   - Microsoft Edge: `edge://extensions`
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select the `extension/` directory from this repository:
   ```
   /path/to/HunterXJob/extension
   ```
5. Pin the **HunterXJob ⚡** icon to your browser toolbar!

---

## Keyboard Shortcuts

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| <kbd>Alt + Shift + F</kbd> (or <kbd>⌥ + ⇧ + F</kbd> on Mac) | **Autofill Foreground Form** | Instantly fills all detected inputs, dropdowns, radios, and checkboxes on the active application. |
| <kbd>Alt + Shift + R</kbd> (or <kbd>⌥ + ⇧ + R</kbd> on Mac) | **Toggle Review Drawer** | Opens the in-page review panel listing all filled fields and sources for inspection. |

*You can customize these shortcuts anytime at `chrome://extensions/shortcuts`.*

---

## CLI Management

From the repository root:

- **Build / Package Extension ZIP**:
  ```bash
  ./hunterx extension build
  ```
  *(Packages the extension into `releases/HunterXJob-Extension-v1.0.0.zip`)*

- **Sync Latest Profile & Vault to Extension**:
  ```bash
  ./hunterx extension sync
  ```

- **Display Extension Info & Quick Start**:
  ```bash
  ./hunterx extension info
  ```

---

## Architecture & File Structure

```
extension/
├── manifest.json              # Manifest V3 configuration with shortcuts & host permissions
├── icons/                     # 16, 32, 48, 128px toolbar & store icons
├── content/
│   ├── detectors.js           # Multi-platform detector (Indeed, LinkedIn, GC Jobs, Workday, etc.)
│   ├── autofill_engine.js     # Framework event dispatcher & answer vault matcher
│   ├── floating_hud.js        # Draggable on-screen HUD & pre-submission review drawer
│   ├── content.js             # Content script coordinator & shortcut listeners
│   └── content.css            # Styles for in-page HUD, review drawer, and field halos
├── popup/
│   ├── popup.html             # Toolbar action popup
│   ├── popup.css              # Dark theme styled layout
│   └── popup.js               # Popup controller & daemon health checker
├── options/
│   ├── options.html           # Settings & profile vault explorer
│   ├── options.css
│   └── options.js             # Config manager & live daemon sync
├── background/
│   └── service_worker.js      # Global command listener & tab injector
└── data/
    ├── default_profile.json   # Embedded portable verified candidate profile
    └── default_vault.json     # Embedded portable master answer vault policies
```
