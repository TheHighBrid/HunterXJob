/**
 * HunterXJob Background Service Worker
 * Manages global keyboard shortcuts, context menus, and background communications.
 */

// Initialize default settings and context menus on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(["daemonUrl"], (res) => {
    if (!res.daemonUrl) {
      chrome.storage.local.set({
        daemonUrl: "http://localhost:8000",
        apiKey: "",
        autoDetectEnabled: true,
      });
    }
  });

  // Create context menu item
  chrome.contextMenus.create({
    id: "hunterx-autofill-context",
    title: "⚡ HunterXJob: Autofill Active Form (Alt+Shift+F)",
    contexts: ["page", "editable", "selection"],
  });
});

// Handle Context Menu click
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "hunterx-autofill-context" && tab.id) {
    sendActionToTab(tab.id, "HUNTERX_TRIGGER_AUTOFILL");
  }
});

// Handle Global Keyboard Commands (defined in manifest.json)
chrome.commands.onCommand.addListener((command) => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs || tabs.length === 0 || !tabs[0].id) return;
    const activeTab = tabs[0];

    if (command === "autofill-active-form") {
      sendActionToTab(activeTab.id, "HUNTERX_TRIGGER_AUTOFILL");
    } else if (command === "toggle-review-panel") {
      sendActionToTab(activeTab.id, "HUNTERX_TOGGLE_REVIEW");
    }
  });
});

/**
 * Safely send an action message to the content script in a tab.
 */
function sendActionToTab(tabId, action) {
  chrome.tabs.sendMessage(tabId, { action }, (response) => {
    if (chrome.runtime.lastError) {
      // Script may not be injected yet on this page, inject manually
      chrome.scripting.executeScript(
        {
          target: { tabId },
          files: [
            "content/detectors.js",
            "content/autofill_engine.js",
            "content/floating_hud.js",
            "content/content.js",
          ],
        },
        () => {
          chrome.scripting.insertCSS({
            target: { tabId },
            files: ["content/content.css"],
          }, () => {
            setTimeout(() => {
              chrome.tabs.sendMessage(tabId, { action });
            }, 100);
          });
        }
      );
    }
  });
}
