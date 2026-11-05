/**
 * HunterXJob Extension Popup Controller
 */
document.addEventListener("DOMContentLoaded", async () => {
  const statusIndicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const platformBadge = document.getElementById("platform-badge");
  const jobTitleEl = document.getElementById("job-title");
  const jobCompanyEl = document.getElementById("job-company");
  const inputCountEl = document.getElementById("input-count");
  const btnAutofill = document.getElementById("btn-autofill");
  const btnToggleHud = document.getElementById("btn-toggle-hud");
  const btnOpenOptions = document.getElementById("btn-open-options");
  const toastArea = document.getElementById("toast-area");

  // Check daemon connection
  try {
    const config = await chrome.storage.local.get(["daemonUrl", "apiKey"]);
    const baseUrl = config.daemonUrl || "http://localhost:8000";
    const res = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(1000) });
    if (res.ok) {
      statusIndicator.classList.remove("offline");
      statusText.textContent = "Daemon Live";
      statusIndicator.title = `Connected to local HunterXJob daemon at ${baseUrl}`;
    } else {
      setStandaloneMode();
    }
  } catch {
    setStandaloneMode();
  }

  function setStandaloneMode() {
    statusIndicator.classList.add("offline");
    statusText.textContent = "Standalone (Portable)";
    statusIndicator.title = "Operating portably with embedded verified profile & answer vault.";
  }

  // Get active tab info
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    chrome.tabs.sendMessage(tab.id, { action: "HUNTERX_GET_PAGE_INFO" }, (response) => {
      if (chrome.runtime.lastError || !response) {
        platformBadge.textContent = "Page Ready";
        jobTitleEl.textContent = tab.title || "Foreground Tab";
        jobCompanyEl.textContent = new URL(tab.url || "about:blank").hostname;
        inputCountEl.textContent = "Ready to scan";
        return;
      }

      const { meta, platform, inputCount } = response;
      if (platform && platform.name) {
        platformBadge.textContent = platform.name;
      }
      if (meta) {
        jobTitleEl.textContent = meta.title || tab.title || "Job Application";
        jobCompanyEl.textContent = meta.company || "";
      }
      inputCountEl.textContent = `${inputCount} controls found`;
    });
  }

  // Autofill button click
  btnAutofill.addEventListener("click", async () => {
    if (!tab || !tab.id) return;
    btnAutofill.disabled = true;
    btnAutofill.querySelector(".btn-label").textContent = "Filling...";

    chrome.tabs.sendMessage(tab.id, { action: "HUNTERX_TRIGGER_AUTOFILL" }, (response) => {
      btnAutofill.disabled = false;
      btnAutofill.querySelector(".btn-label").textContent = "Autofill Application";

      if (response && response.success && response.report) {
        const count = response.report.filled ? response.report.filled.length : 0;
        showToast(`✓ Filled ${count} fields! Review highlights in page before submitting.`);
      } else {
        showToast("✓ Autofill triggered. Please review the foreground form!");
      }
    });
  });

  // Toggle HUD / Review Drawer
  btnToggleHud.addEventListener("click", () => {
    if (tab && tab.id) {
      chrome.tabs.sendMessage(tab.id, { action: "HUNTERX_TOGGLE_REVIEW" });
      window.close();
    }
  });

  // Open Options
  btnOpenOptions.addEventListener("click", () => {
    if (chrome.runtime.openOptionsPage) {
      chrome.runtime.openOptionsPage();
    } else {
      window.open(chrome.runtime.getURL("options/options.html"));
    }
  });

  function showToast(msg) {
    toastArea.textContent = msg;
    toastArea.style.display = "block";
    setTimeout(() => {
      toastArea.style.display = "none";
    }, 3500);
  }
});
