/**
 * HunterXJob Content Script Coordinator
 * Bridges browser runtime messages, hotkey keyboard shortcuts,
 * dynamic SPA multi-step transitions, and candidate profile synchronization.
 */
(() => {
  async function loadData() {
    let profile = null;
    let vault = null;

    // 1. Try chrome.storage.local first
    try {
      const stored = await chrome.storage.local.get(["profile", "vault", "daemonUrl", "apiKey"]);
      if (stored.profile && stored.vault) {
        profile = stored.profile;
        vault = stored.vault;
      }
    } catch (e) {
      console.warn("[HunterXJob] Storage lookup fallback:", e);
    }

    // 2. If missing, load bundled default files
    if (!profile || !vault) {
      try {
        const pUrl = chrome.runtime.getURL("data/default_profile.json");
        const vUrl = chrome.runtime.getURL("data/default_vault.json");
        const [pRes, vRes] = await Promise.all([fetch(pUrl), fetch(vUrl)]);
        profile = await pRes.json();
        vault = await vRes.json();
        chrome.storage.local.set({ profile, vault });
      } catch (err) {
        console.error("[HunterXJob] Failed to load default bundled profile/vault:", err);
      }
    }

    // 3. Opportunistic background sync with local HunterXJob daemon if running
    try {
      const config = await chrome.storage.local.get(["daemonUrl", "apiKey"]);
      const baseUrl = config.daemonUrl || "http://localhost:8000";
      const headers = { "Content-Type": "application/json" };
      if (config.apiKey) headers["X-API-Key"] = config.apiKey;

      const syncRes = await fetch(`${baseUrl}/api/autofill/bundle`, {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(1500),
      });

      if (syncRes.ok) {
        const bundle = await syncRes.json();
        if (bundle.profile && bundle.vault) {
          profile = bundle.profile;
          vault = bundle.vault;
          chrome.storage.local.set({ profile, vault, daemonConnected: true });
          console.log("[HunterXJob] Synchronized live profile and vault from daemon.");
        }
      }
    } catch {
      // Local daemon is not running; standalone portable mode remains fully active.
    }

    return { profile, vault };
  }

  async function bootstrap() {
    const { profile, vault } = await loadData();
    if (window.HunterXEngine) {
      await window.HunterXEngine.init(profile, vault);
    }

    const platform = window.HunterXDetectors ? window.HunterXDetectors.detectPlatform() : { isJobPage: false };

    // Inject Floating HUD if on a detected job page, form, or portal
    if (platform.isJobPage || document.querySelector("form, [role='dialog'], #ia-container, .jobs-easy-apply-modal")) {
      if (window.HunterXHUD) {
        window.HunterXHUD.init();
      }
    }

    // In-page keyboard shortcut listener (Alt+Shift+F & Alt+Shift+R)
    window.addEventListener("keydown", (e) => {
      // Alt+Shift+F (or Option+Shift+F on Mac)
      if (e.altKey && e.shiftKey && (e.key === "F" || e.key === "f" || e.code === "KeyF")) {
        e.preventDefault();
        if (window.HunterXHUD) {
          window.HunterXHUD.triggerAutofill();
        } else if (window.HunterXEngine) {
          const container = window.HunterXDetectors ? window.HunterXDetectors.getForegroundContainer() : document.body;
          window.HunterXEngine.autofill(container);
        }
      }

      // Alt+Shift+R (or Option+Shift+R on Mac)
      if (e.altKey && e.shiftKey && (e.key === "R" || e.key === "r" || e.code === "KeyR")) {
        e.preventDefault();
        if (window.HunterXHUD) {
          window.HunterXHUD.toggleReviewDrawer();
        }
      }
    });

    // Listen for runtime messages from extension popup or background worker
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === "HUNTERX_TRIGGER_AUTOFILL") {
        if (window.HunterXHUD) {
          window.HunterXHUD.triggerAutofill().then((report) => sendResponse({ success: true, report }));
        } else if (window.HunterXEngine) {
          const container = window.HunterXDetectors ? window.HunterXDetectors.getForegroundContainer() : document.body;
          const report = window.HunterXEngine.autofill(container);
          sendResponse({ success: true, report });
        }
        return true;
      }

      if (request.action === "HUNTERX_TOGGLE_REVIEW") {
        if (window.HunterXHUD) {
          window.HunterXHUD.toggleReviewDrawer();
        }
        sendResponse({ success: true });
        return true;
      }

      if (request.action === "HUNTERX_GET_PAGE_INFO") {
        const meta = window.HunterXDetectors ? window.HunterXDetectors.extractJobMetadata() : {};
        const platform = window.HunterXDetectors ? window.HunterXDetectors.detectPlatform() : {};
        const container = window.HunterXDetectors ? window.HunterXDetectors.getForegroundContainer() : document.body;
        const inputCount = container.querySelectorAll("input, textarea, select, [role='combobox']").length;
        sendResponse({
          meta,
          platform,
          inputCount,
          lastReport: window.HunterXEngine ? window.HunterXEngine.activeReport : null,
        });
        return true;
      }
    });

    // Observe DOM mutations for multi-step applications (e.g. Next step clicked on LinkedIn/Indeed)
    const observer = new MutationObserver(() => {
      const openDialog = document.querySelector(".jobs-easy-apply-modal, #ia-container, dialog[open]");
      if (openDialog && window.HunterXHUD && !document.getElementById("hunterx-floating-hud")) {
        window.HunterXHUD.init();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
