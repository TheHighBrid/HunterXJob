/**
 * HunterXJob In-Page Floating HUD & Review Drawer
 * Provides immediate on-screen access to autofill, detection status,
 * and a pre-submission review panel right inside the job application page.
 */
(() => {
  class FloatingHUD {
    constructor() {
      this.hudEl = null;
      this.reviewDrawerEl = null;
      this.isMinimized = false;
      this.lastReport = null;
    }

    init() {
      if (document.getElementById("hunterx-floating-hud")) return;
      this.render();
      this.attachEvents();
    }

    render() {
      const platform = window.HunterXDetectors ? window.HunterXDetectors.detectPlatform() : { name: "Job Form", id: "generic" };

      // Root HUD Container
      this.hudEl = document.createElement("div");
      this.hudEl.id = "hunterx-floating-hud";
      this.hudEl.className = "hunterx-hud-container";
      this.hudEl.innerHTML = `
        <div class="hunterx-hud-pill" id="hunterx-hud-handle">
          <div class="hunterx-hud-brand">
            <span class="hunterx-hud-logo">⚡</span>
            <span class="hunterx-hud-title">HunterX</span>
          </div>
          <div class="hunterx-hud-platform" title="${escapeHtml(platform.name)}">
            <span class="hx-pulse-dot"></span>
            <span class="hx-platform-name">${escapeHtml(platform.name)}</span>
          </div>
          <button class="hunterx-hud-btn hunterx-hud-btn-primary" id="hunterx-btn-fill" title="Autofill Foreground Form (Alt+Shift+F)">
            <span>Fill Form</span>
            <kbd class="hx-kbd">⌥⇧F</kbd>
          </button>
          <button class="hunterx-hud-btn hunterx-hud-btn-secondary" id="hunterx-btn-review" title="Review filled fields before submitting" style="display:none;">
            <span>Review (<span id="hunterx-count-filled">0</span>)</span>
          </button>
          <button class="hunterx-hud-btn-icon" id="hunterx-btn-minimize" title="Minimize / Expand">
            −
          </button>
        </div>

        <!-- Pre-Submission Review Drawer -->
        <div class="hunterx-review-drawer" id="hunterx-review-drawer" style="display: none;">
          <div class="hunterx-drawer-header">
            <div class="hunterx-drawer-title">
              <span class="hx-check-icon">✓</span>
              <span>Pre-Submission Review</span>
            </div>
            <span class="hunterx-drawer-badge" id="hunterx-review-badge">Ready to Review</span>
            <button class="hunterx-drawer-close" id="hunterx-drawer-close">✕</button>
          </div>
          <div class="hunterx-drawer-alert">
            <strong>Truth Guard Active:</strong> Review all answers before submitting. HunterXJob never auto-submits your applications.
          </div>
          <div class="hunterx-drawer-content" id="hunterx-review-list">
            <!-- Items injected dynamically -->
          </div>
          <div class="hunterx-drawer-footer">
            <button class="hunterx-drawer-btn" id="hunterx-drawer-btn-refill">↻ Re-scan Form</button>
            <span class="hx-ready-hint">✓ Ready to send? Review above and click Submit in the form.</span>
          </div>
        </div>
      `;

      document.body.appendChild(this.hudEl);
      this.reviewDrawerEl = this.hudEl.querySelector("#hunterx-review-drawer");
      this.makeDraggable(this.hudEl.querySelector("#hunterx-hud-handle"));
    }

    attachEvents() {
      const fillBtn = this.hudEl.querySelector("#hunterx-btn-fill");
      const reviewBtn = this.hudEl.querySelector("#hunterx-btn-review");
      const minimizeBtn = this.hudEl.querySelector("#hunterx-btn-minimize");
      const closeDrawerBtn = this.hudEl.querySelector("#hunterx-drawer-close");
      const refillBtn = this.hudEl.querySelector("#hunterx-drawer-btn-refill");

      if (fillBtn) {
        fillBtn.addEventListener("click", () => this.triggerAutofill());
      }
      if (reviewBtn) {
        reviewBtn.addEventListener("click", () => this.toggleReviewDrawer());
      }
      if (minimizeBtn) {
        minimizeBtn.addEventListener("click", () => this.toggleMinimize());
      }
      if (closeDrawerBtn) {
        closeDrawerBtn.addEventListener("click", () => {
          this.reviewDrawerEl.style.display = "none";
        });
      }
      if (refillBtn) {
        refillBtn.addEventListener("click", () => this.triggerAutofill());
      }
    }

    toggleMinimize() {
      this.isMinimized = !this.isMinimized;
      const pill = this.hudEl.querySelector(".hunterx-hud-pill");
      const minBtn = this.hudEl.querySelector("#hunterx-btn-minimize");
      if (this.isMinimized) {
        pill.classList.add("hunterx-minimized");
        minBtn.textContent = "+";
        this.reviewDrawerEl.style.display = "none";
      } else {
        pill.classList.remove("hunterx-minimized");
        minBtn.textContent = "−";
      }
    }

    toggleReviewDrawer() {
      const isVisible = this.reviewDrawerEl.style.display !== "none";
      this.reviewDrawerEl.style.display = isVisible ? "none" : "flex";
      if (!isVisible && this.lastReport) {
        this.renderReviewItems(this.lastReport);
      }
    }

    async triggerAutofill() {
      const fillBtn = this.hudEl.querySelector("#hunterx-btn-fill");
      if (fillBtn) {
        fillBtn.innerHTML = `<span>Filling...</span>`;
      }

      const container = window.HunterXDetectors ? window.HunterXDetectors.getForegroundContainer() : document.body;
      const report = window.HunterXEngine ? window.HunterXEngine.autofill(container) : { filled: [], skipped: [] };
      this.lastReport = report;

      // Update Fill Button & Count
      if (fillBtn) {
        fillBtn.innerHTML = `<span>Filled!</span> <kbd class="hx-kbd">⌥⇧F</kbd>`;
        setTimeout(() => {
          fillBtn.innerHTML = `<span>Fill Form</span> <kbd class="hx-kbd">⌥⇧F</kbd>`;
        }, 2000);
      }

      // Update Review Button
      const reviewBtn = this.hudEl.querySelector("#hunterx-btn-review");
      const countEl = this.hudEl.querySelector("#hunterx-count-filled");
      if (reviewBtn && countEl) {
        countEl.textContent = report.filled.length;
        reviewBtn.style.display = "inline-flex";
      }

      // Show toast
      this.showToast(`⚡ HunterXJob filled ${report.filled.length} fields. Please review before sending!`);

      // Open review drawer automatically on first fill so user sees review list
      this.reviewDrawerEl.style.display = "flex";
      this.renderReviewItems(report);

      return report;
    }

    renderReviewItems(report) {
      const listEl = this.hudEl.querySelector("#hunterx-review-list");
      if (!listEl) return;

      if (!report || report.filled.length === 0) {
        listEl.innerHTML = `<div class="hx-empty-state">No matching form fields were detected in the active container.</div>`;
        return;
      }

      const itemsHtml = report.filled.map(item => `
        <div class="hx-review-row">
          <div class="hx-review-field">
            <span class="hx-field-name">${escapeHtml(item.label || "Field")}</span>
            <span class="hx-field-source">${escapeHtml(item.source)}</span>
          </div>
          <div class="hx-review-val" title="${escapeHtml(String(item.value))}">
            ${escapeHtml(String(item.value))}
          </div>
        </div>
      `).join("");

      listEl.innerHTML = itemsHtml;
    }

    showToast(message) {
      const toast = document.createElement("div");
      toast.className = "hunterx-hud-toast";
      toast.textContent = message;
      document.body.appendChild(toast);
      setTimeout(() => toast.classList.add("hx-toast-show"), 10);
      setTimeout(() => {
        toast.classList.remove("hx-toast-show");
        setTimeout(() => toast.remove(), 400);
      }, 4000);
    }

    makeDraggable(handle) {
      let posX = 0, posY = 0, mouseX = 0, mouseY = 0;
      handle.onmousedown = (e) => {
        if (e.target.tagName.toLowerCase() === "button" || e.target.closest("button")) return;
        e.preventDefault();
        mouseX = e.clientX;
        mouseY = e.clientY;
        document.onmouseup = () => {
          document.onmouseup = null;
          document.onmousemove = null;
        };
        document.onmousemove = (e) => {
          e.preventDefault();
          posX = mouseX - e.clientX;
          posY = mouseY - e.clientY;
          mouseX = e.clientX;
          mouseY = e.clientY;
          this.hudEl.style.top = (this.hudEl.offsetTop - posY) + "px";
          this.hudEl.style.left = (this.hudEl.offsetLeft - posX) + "px";
          this.hudEl.style.right = "auto";
          this.hudEl.style.bottom = "auto";
        };
      };
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.HunterXHUD = new FloatingHUD();
})();
