/**
 * HunterXJob Options / Settings Controller
 */
document.addEventListener("DOMContentLoaded", async () => {
  const daemonUrlInput = document.getElementById("daemon-url");
  const apiKeyInput = document.getElementById("api-key");
  const btnSave = document.getElementById("btn-save");
  const btnTestDaemon = document.getElementById("btn-test-daemon");
  const testStatus = document.getElementById("test-status");
  const btnSyncDaemon = document.getElementById("btn-sync-daemon");
  const btnResetDefaults = document.getElementById("btn-reset-defaults");

  const profileName = document.getElementById("profile-name");
  const profileEmail = document.getElementById("profile-email");
  const profilePhone = document.getElementById("profile-phone");
  const profileLocation = document.getElementById("profile-location");

  const vaultFilter = document.getElementById("vault-filter");
  const vaultTbody = document.getElementById("vault-tbody");

  let currentProfile = {};
  let currentVault = {};

  // Load saved settings
  const stored = await chrome.storage.local.get(["daemonUrl", "apiKey", "profile", "vault"]);
  if (stored.daemonUrl) daemonUrlInput.value = stored.daemonUrl;
  if (stored.apiKey) apiKeyInput.value = stored.apiKey;

  if (stored.profile) {
    currentProfile = stored.profile;
  } else {
    const pRes = await fetch(chrome.runtime.getURL("data/default_profile.json"));
    currentProfile = await pRes.json();
  }

  if (stored.vault) {
    currentVault = stored.vault;
  } else {
    const vRes = await fetch(chrome.runtime.getURL("data/default_vault.json"));
    currentVault = await vRes.json();
  }

  renderProfile(currentProfile);
  renderVault(currentVault);

  // Save settings
  btnSave.addEventListener("click", () => {
    chrome.storage.local.set({
      daemonUrl: daemonUrlInput.value.trim(),
      apiKey: apiKeyInput.value.trim(),
    }, () => {
      btnSave.textContent = "Saved ✓";
      setTimeout(() => btnSave.textContent = "Save Settings", 2000);
    });
  });

  // Test daemon connection
  btnTestDaemon.addEventListener("click", async () => {
    testStatus.textContent = "Testing...";
    testStatus.style.color = "#94a3b8";

    try {
      const url = daemonUrlInput.value.trim() || "http://localhost:8000";
      const headers = {};
      if (apiKeyInput.value.trim()) headers["X-API-Key"] = apiKeyInput.value.trim();

      const res = await fetch(`${url}/api/health`, { headers, signal: AbortSignal.timeout(2000) });
      if (res.ok) {
        testStatus.textContent = "Connected successfully! ✓";
        testStatus.style.color = "#10b981";
      } else {
        testStatus.textContent = `Daemon error (Status: ${res.status})`;
        testStatus.style.color = "#ef4444";
      }
    } catch (err) {
      testStatus.textContent = "Cannot reach daemon. Is './hunterx daemon' running?";
      testStatus.style.color = "#f59e0b";
    }
  });

  // Sync from Daemon
  btnSyncDaemon.addEventListener("click", async () => {
    btnSyncDaemon.textContent = "Syncing...";
    try {
      const url = daemonUrlInput.value.trim() || "http://localhost:8000";
      const headers = {};
      if (apiKeyInput.value.trim()) headers["X-API-Key"] = apiKeyInput.value.trim();

      const res = await fetch(`${url}/api/autofill/bundle`, { headers, signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        const bundle = await res.json();
        currentProfile = bundle.profile;
        currentVault = bundle.vault;
        chrome.storage.local.set({ profile: currentProfile, vault: currentVault });
        renderProfile(currentProfile);
        renderVault(currentVault);
        btnSyncDaemon.textContent = "Synced ✓";
        setTimeout(() => btnSyncDaemon.textContent = "↻ Sync from Running Daemon", 2000);
      } else {
        alert("Failed to sync from daemon: Status " + res.status);
        btnSyncDaemon.textContent = "↻ Sync from Running Daemon";
      }
    } catch (e) {
      alert("Error reaching daemon at " + daemonUrlInput.value);
      btnSyncDaemon.textContent = "↻ Sync from Running Daemon";
    }
  });

  // Reset to Defaults
  btnResetDefaults.addEventListener("click", async () => {
    if (confirm("Reset profile and vault to bundled defaults?")) {
      const [pRes, vRes] = await Promise.all([
        fetch(chrome.runtime.getURL("data/default_profile.json")),
        fetch(chrome.runtime.getURL("data/default_vault.json")),
      ]);
      currentProfile = await pRes.json();
      currentVault = await vRes.json();
      chrome.storage.local.set({ profile: currentProfile, vault: currentVault });
      renderProfile(currentProfile);
      renderVault(currentVault);
    }
  });

  // Vault Filter
  vaultFilter.addEventListener("input", () => {
    renderVault(currentVault, vaultFilter.value.trim());
  });

  function renderProfile(profile) {
    const c = profile.contact || {};
    profileName.textContent = profile.full_name || `${c.first_name || ""} ${c.last_name || ""}`;
    profileEmail.textContent = c.email || "Not set";
    profilePhone.textContent = c.phone || "Not set";
    profileLocation.textContent = c.location || "Not set";
  }

  function renderVault(vault, query = "") {
    vaultTbody.innerHTML = "";
    const filter = query.toLowerCase();

    const keys = Object.keys(vault).sort();
    for (const key of keys) {
      const item = vault[key];
      const valStr = String(item.value || "");
      if (filter && !key.toLowerCase().includes(filter) && !valStr.toLowerCase().includes(filter)) {
        continue;
      }

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${escapeHtml(key)}</strong></td>
        <td>${escapeHtml(valStr)}</td>
        <td><span class="badge-scope">${escapeHtml(item.scope || "global")}</span></td>
        <td>${Math.round((item.confidence || 1.0) * 100)}%</td>
      `;
      vaultTbody.appendChild(tr);
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
});
