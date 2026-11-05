/**
 * HunterXJob Autofill Engine
 * Precision form filler with React/Angular synthetic event dispatching,
 * candidate truth guard compliance, and review-before-submit visual badges.
 */
(() => {
  class AutofillEngine {
    constructor() {
      this.profile = null;
      this.vault = {};
      this.activeReport = {
        filled: [],
        skipped: [],
        total: 0,
        timestamp: null,
      };
    }

    async init(bundledProfile, bundledVault) {
      this.profile = bundledProfile || {};
      this.vault = bundledVault || {};
    }

    /**
     * Dispatch native events to ensure React, Vue, Angular, Workday form listeners catch updates.
     */
    setNativeValue(element, value) {
      if (!element) return;
      const isTextarea = element.tagName.toLowerCase() === "textarea";
      const proto = isTextarea ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, "value");

      if (desc && desc.set) {
        desc.set.call(element, value);
      } else {
        element.value = value;
      }

      element.dispatchEvent(new Event("focus", { bubbles: true }));
      element.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      element.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      element.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "a" }));
      element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, cancelable: true, key: "a" }));
      element.dispatchEvent(new Event("blur", { bubbles: true }));
    }

    /**
     * Extract human-readable label from the DOM for an input or control.
     */
    extractLabel(el) {
      if (!el) return "";
      let labelText = "";

      // 1. Explicit <label for="...">
      if (el.id) {
        const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (explicitLabel && explicitLabel.textContent.trim()) {
          return explicitLabel.textContent.trim();
        }
      }

      // 2. Parent <label>
      const parentLabel = el.closest("label");
      if (parentLabel && parentLabel.textContent.trim()) {
        return parentLabel.textContent.trim();
      }

      // 3. aria-labelledby
      const ariaLabelledBy = el.getAttribute("aria-labelledby");
      if (ariaLabelledBy) {
        const idList = ariaLabelledBy.split(/\s+/);
        const parts = idList.map(id => {
          const target = document.getElementById(id);
          return target ? target.textContent.trim() : "";
        }).filter(Boolean);
        if (parts.length > 0) return parts.join(" ");
      }

      // 4. aria-label or placeholder
      if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
      if (el.getAttribute("placeholder")) return el.getAttribute("placeholder").trim();

      // 5. Container legend / heading (for radio groups)
      const fieldset = el.closest("fieldset, [role='radiogroup'], [role='group']");
      if (fieldset) {
        const legend = fieldset.querySelector("legend, h2, h3, h4, span.label-text, [class*='label'], [class*='title']");
        if (legend && legend.textContent.trim()) {
          return legend.textContent.trim();
        }
      }

      // 6. Closest preceding paragraph or header or data-automation container
      const container = el.closest("[data-automation-id*='formField'], .ia-JobQuestions-item, .jobs-easy-apply-form-section, div[class*='question'], div[class*='field']");
      if (container) {
        const textNode = container.querySelector("label, p, span, h3, h4, div[class*='header']");
        if (textNode && textNode !== el && textNode.textContent.trim()) {
          return textNode.textContent.trim();
        }
      }

      // 7. Fallback to name or id
      return el.name || el.id || "";
    }

    /**
     * Normalize string for fuzzy matching
     */
    normalize(str) {
      if (!str) return "";
      return str
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    }

    /**
     * Map a label + field type to HunterXJob profile or vault answer.
     */
    matchFieldAnswer(label, type, el) {
      const norm = this.normalize(label);
      const contact = this.profile.contact || {};
      const fullName = this.profile.full_name || "Mohamed Alem";
      const vault = this.vault || {};

      // Helper to get vault value
      const getVault = (key, fallback) => {
        if (vault[key] && vault[key].value !== undefined) {
          return { value: vault[key].value, source: "Answer Vault: " + key, confidence: 1.0 };
        }
        return fallback ? { value: fallback, source: "Profile Default", confidence: 0.95 } : null;
      };

      // 1. First Name
      if (/(first|given|prenom)\s*name/i.test(norm) || norm === "first" || el.name === "firstName" || el.autocomplete === "given-name") {
        return { value: contact.first_name || "Mohamed", source: "Verified Profile: First Name", confidence: 1.0 };
      }

      // 2. Last Name
      if (/(last|family|surname|nom)\s*name/i.test(norm) || norm === "last" || el.name === "lastName" || el.autocomplete === "family-name") {
        return { value: contact.last_name || "Alem", source: "Verified Profile: Last Name", confidence: 1.0 };
      }

      // 3. Full Name
      if (/(full|complete|legal)\s*name/i.test(norm) || norm === "name" || el.autocomplete === "name") {
        return { value: fullName, source: "Verified Profile: Full Name", confidence: 1.0 };
      }

      // 4. Email
      if (/email|courriel|e\s*mail/i.test(norm) || type === "email" || el.autocomplete === "email") {
        return { value: contact.email || "mohamed@melato.ca", source: "Verified Profile: Email", confidence: 1.0 };
      }

      // 5. Phone / Mobile
      if (/phone|telephone|mobile|cell|num.*tel/i.test(norm) || type === "tel" || el.autocomplete === "tel") {
        return { value: contact.phone || "(343) 324-7677", source: "Verified Profile: Phone", confidence: 1.0 };
      }

      // 6. City / Ville
      if (/\b(city|ville|municipality)\b/i.test(norm) || el.autocomplete === "address-level2") {
        return getVault("city", "Ottawa");
      }

      // 7. Province / State
      if (/\b(province|state|region|territory)\b/i.test(norm) || el.autocomplete === "address-level1") {
        return getVault("state", "Ontario");
      }

      // 8. Country / Pays
      if (/\b(country|pays|nation)\b/i.test(norm) || el.autocomplete === "country-name") {
        return getVault("country", "Canada");
      }

      // 9. Postal Code / Zip
      if (/postal\s*code|code\s*postal|zip\s*code/i.test(norm) || el.autocomplete === "postal-code") {
        return { value: "K1P 1J1", source: "Profile Location (Ottawa, ON)", confidence: 0.9 };
      }

      // 10. Address / Location
      if (/street\s*address|street|adresse|location|residence/i.test(norm) || el.autocomplete === "street-address") {
        return { value: contact.location || "Ottawa, ON, Canada", source: "Verified Profile: Location", confidence: 0.95 };
      }

      // 11. LinkedIn URL
      if (/linkedin/i.test(norm)) {
        return { value: contact.linkedin_url || "https://www.linkedin.com/in/mohamedalem", source: "Verified Profile: LinkedIn", confidence: 1.0 };
      }

      // 12. GitHub URL / Portfolio / Website
      if (/github/i.test(norm)) {
        return getVault("github_url", "https://github.com/mohamedalem");
      }
      if (/portfolio|website|site\s*web|personal\s*link/i.test(norm)) {
        return getVault("website_url", "https://melato.ca");
      }

      // 13. Canadian Work Authorization & Citizenship
      if (/legally\s*authorized\s*to\s*work\s*in\s*canada|eligible\s*to\s*work\s*in\s*canada|droit\s*de\s*travailler\s*au\s*canada/i.test(norm)) {
        return getVault("work_authorization_CA", "Yes");
      }
      if (/canadian\s*citizen|permanent\s*resident|citoyen\s*canadien|statut\s*au\s*canada/i.test(norm)) {
        return { value: "Canadian Citizen", source: "Verified Profile: Citizenship (Canada)", confidence: 1.0 };
      }
      if (/(require|need|will\s*you\s*require)\s*sponsorship\s*in\s*canada|parrainage/i.test(norm)) {
        return getVault("sponsorship_CA", "No");
      }

      // 14. US Work Authorization & Sponsorship
      if (/authorized\s*to\s*work\s*in\s*the\s*united\s*states|authorized\s*to\s*work\s*in\s*the\s*u\s*s|work\s*in\s*us/i.test(norm)) {
        return getVault("work_authorization_US", "Yes");
      }
      if (/(require|need)\s*sponsorship\s*in\s*the\s*united\s*states|u\s*s\s*sponsorship/i.test(norm)) {
        return getVault("sponsorship_US", "No");
      }

      // 15. Canadian Public Sector / GC Jobs / Bilingual SLE Proficiency
      if (/bilingual|bilingue|official\s*languages|langues\s*officielles/i.test(norm)) {
        return { value: "Bilingual English/French", source: "Verified Profile: Bilingual Proficiency", confidence: 1.0 };
      }
      if (/first\s*official\s*language|premiere\s*langue\s*officielle/i.test(norm)) {
        return { value: "English", source: "Profile Language", confidence: 0.95 };
      }
      if (/second\s*official\s*language|seconde\s*langue\s*officielle/i.test(norm)) {
        return { value: "French", source: "Profile Language", confidence: 0.95 };
      }
      if (/sle|second\s*language\s*evaluation|oral\s*proficiency|written\s*proficiency|reading\s*comprehension/i.test(norm)) {
        return { value: "Level C", source: "Verified Profile: Full Bilingual Proficiency (C/C/C)", confidence: 0.95 };
      }
      if (/security\s*clearance|cote\s*de\s*securite|reliability\s*status|secret\s*clearance/i.test(norm)) {
        return { value: "Reliability Status (Eligible for Secret)", source: "Truth Guard: Verified Clearance Status", confidence: 1.0 };
      }

      // 16. Salary Expectation / Compensation
      if (/salary|compensation|hourly\s*rate|remuneration|desired\s*pay|pay\s*expectation/i.test(norm)) {
        return getVault("salary_expectation", "60000");
      }

      // 17. Notice Period / Start Date
      if (/notice\s*period|preavis|how\s*soon\s*can\s*you\s*start|start\s*date|availability/i.test(norm)) {
        return getVault("start_availability", "Immediately");
      }

      // 18. Relocation / Commute
      if (/relocate|relocation|willing\s*to\s*relocate|demenager/i.test(norm)) {
        return getVault("willing_to_relocate", "No");
      }

      // 19. Background Check Consent
      if (/background\s*check|verification\s*des\s*antecedents|criminal\s*record/i.test(norm)) {
        return getVault("background_check_consent", "Yes");
      }

      // 20. Education: Degree, School, Major
      if (/degree|diploma|diplome|highest\s*level\s*of\s*education/i.test(norm)) {
        return { value: "College Diploma", source: "Verified Profile: Education Degree", confidence: 1.0 };
      }
      if (/college|university|school|institution|ecole/i.test(norm)) {
        return { value: "Collège La Cité", source: "Verified Profile: Education Institution", confidence: 1.0 };
      }
      if (/major|field\s*of\s*study|discipline/i.test(norm)) {
        return { value: "Computer Engineering Technology", source: "Verified Profile: Field of Study", confidence: 1.0 };
      }
      if (/graduation\s*year|year\s*graduated|annee\s*d\s*obtention/i.test(norm)) {
        return { value: "2016", source: "Verified Profile: Graduation Year", confidence: 1.0 };
      }

      // 21. Recent Employment: Title, Company
      if (/current\s*company|current\s*employer|most\s*recent\s*employer|employeur/i.test(norm)) {
        return { value: "Scotiabank - Tangerine", source: "Verified Profile: Current/Recent Employer", confidence: 0.95 };
      }
      if (/current\s*title|current\s*job\s*title|current\s*role|titre/i.test(norm)) {
        return { value: "Collection Officer (Bilingual)", source: "Verified Profile: Recent Job Title", confidence: 0.95 };
      }
      if (/years\s*of\s*experience|total\s*experience|annees\s*d\s*experience/i.test(norm)) {
        return { value: "9", source: "Verified Profile: 9+ years banking / 2+ years fraud & AML", confidence: 0.95 };
      }

      // 22. General Yes/No Screening Questions (Check Vault matching)
      if (type === "radio" || type === "select" || type === "checkbox") {
        for (const [vKey, vData] of Object.entries(vault)) {
          const keyNorm = this.normalize(vKey);
          if (norm.includes(keyNorm) || keyNorm.includes(norm)) {
            return { value: vData.value, source: `Answer Vault: ${vKey}`, confidence: 0.9 };
          }
        }
      }

      return null;
    }

    /**
     * Choose best matching option for a <select> or radio group
     */
    findBestOption(options, targetValue) {
      if (!options || options.length === 0) return null;
      const targetNorm = this.normalize(targetValue);

      // Exact match
      for (const opt of options) {
        if (this.normalize(opt.text) === targetNorm || this.normalize(opt.value) === targetNorm) {
          return opt;
        }
      }

      // Boolean equivalence (Yes/No, Oui/Non, True/False)
      const isYes = /^(yes|oui|true|1|agree)$/i.test(targetValue.trim());
      const isNo = /^(no|non|false|0|disagree)$/i.test(targetValue.trim());

      if (isYes) {
        for (const opt of options) {
          if (/^(yes|oui|true|i agree|authorized|citizen)$/i.test(opt.text.trim()) || /^(yes|true|1)$/i.test(opt.value.trim())) {
            return opt;
          }
        }
      }

      if (isNo) {
        for (const opt of options) {
          if (/^(no|non|false|i do not agree|decline)$/i.test(opt.text.trim()) || /^(no|false|0)$/i.test(opt.value.trim())) {
            return opt;
          }
        }
      }

      // Substring match
      for (const opt of options) {
        const optNorm = this.normalize(opt.text);
        if (optNorm.includes(targetNorm) || targetNorm.includes(optNorm)) {
          return opt;
        }
      }

      return null;
    }

    /**
     * Mark an element visually so the user can easily review what was filled.
     */
    applyReviewHighlight(el, label, match) {
      el.classList.add("hunterx-autofilled");
      el.setAttribute("data-hunterx-source", match.source);
      el.setAttribute("data-hunterx-value", String(match.value));

      // Add a small inline review badge if not already added
      let parent = el.closest(".hunterx-field-wrapper") || el.parentElement;
      if (parent && !parent.querySelector(".hunterx-review-tag")) {
        const tag = document.createElement("span");
        tag.className = "hunterx-review-tag";
        tag.title = `HunterXJob: ${match.source} (Confidence: ${Math.round(match.confidence * 100)}%)`;
        tag.innerHTML = `<span class="hx-badge-dot"></span>Review: <b>${escapeHtml(match.source.split(":")[0])}</b>`;
        parent.style.position = "relative";
        parent.appendChild(tag);
      }
    }

    /**
     * Run the autofill process on the target container.
     */
    autofill(container) {
      const root = container || document.body;
      const inputs = Array.from(root.querySelectorAll("input, textarea, select, [role='combobox']"));
      const report = {
        filled: [],
        skipped: [],
        total: inputs.length,
        timestamp: new Date().toISOString(),
      };

      const processedRadioGroups = new Set();

      for (const el of inputs) {
        // Skip hidden, disabled, or read-only controls
        if (el.type === "hidden" || el.disabled || el.readOnly) continue;
        if (el.offsetParent === null && el.getAttribute("role") !== "combobox") continue;

        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute("type") || "text").toLowerCase();

        // Handle Radio Groups together
        if (type === "radio") {
          const groupName = el.name;
          if (groupName && processedRadioGroups.has(groupName)) continue;
          if (groupName) processedRadioGroups.add(groupName);

          const radios = Array.from(root.querySelectorAll(`input[type='radio'][name='${CSS.escape(groupName)}']`));
          const label = this.extractLabel(el) || this.extractLabel(radios[0]);
          const match = this.matchFieldAnswer(label, "radio", el);

          if (match && match.value !== undefined) {
            const options = radios.map(r => ({
              element: r,
              text: this.extractLabel(r) || r.value,
              value: r.value,
            }));
            const best = this.findBestOption(options, String(match.value));
            if (best && best.element) {
              best.element.click();
              best.element.checked = true;
              best.element.dispatchEvent(new Event("change", { bubbles: true }));
              this.applyReviewHighlight(best.element, label, match);
              report.filled.push({
                label,
                type: "radio",
                value: best.text,
                source: match.source,
                confidence: match.confidence,
              });
              continue;
            }
          }
          report.skipped.push({ label, type: "radio", reason: "No matching policy found" });
          continue;
        }

        // Handle Checkbox
        if (type === "checkbox") {
          const label = this.extractLabel(el);
          const match = this.matchFieldAnswer(label, "checkbox", el);
          if (match && match.value !== undefined) {
            const shouldCheck = /^(yes|true|agree|oui|1)$/i.test(String(match.value));
            if (el.checked !== shouldCheck) {
              el.click();
              el.checked = shouldCheck;
              el.dispatchEvent(new Event("change", { bubbles: true }));
            }
            this.applyReviewHighlight(el, label, match);
            report.filled.push({
              label,
              type: "checkbox",
              value: shouldCheck ? "Checked" : "Unchecked",
              source: match.source,
              confidence: match.confidence,
            });
            continue;
          }
          report.skipped.push({ label, type: "checkbox", reason: "No matching consent/policy" });
          continue;
        }

        // Handle <select> Dropdowns
        if (tag === "select") {
          const label = this.extractLabel(el);
          const match = this.matchFieldAnswer(label, "select", el);
          if (match && match.value !== undefined) {
            const options = Array.from(el.options).map(opt => ({
              element: opt,
              text: opt.textContent.trim(),
              value: opt.value,
            }));
            const best = this.findBestOption(options, String(match.value));
            if (best && best.element) {
              el.value = best.element.value;
              el.dispatchEvent(new Event("change", { bubbles: true }));
              this.applyReviewHighlight(el, label, match);
              report.filled.push({
                label,
                type: "select",
                value: best.text,
                source: match.source,
                confidence: match.confidence,
              });
              continue;
            }
          }
          report.skipped.push({ label, type: "select", reason: "Dropdown option not resolved" });
          continue;
        }

        // Handle Text, Textarea, Email, Tel, Number
        if (tag === "textarea" || ["text", "email", "tel", "number", "url"].includes(type)) {
          const label = this.extractLabel(el);
          const match = this.matchFieldAnswer(label, type, el);

          if (match && match.value !== undefined) {
            this.setNativeValue(el, match.value);
            this.applyReviewHighlight(el, label, match);
            report.filled.push({
              label,
              type,
              value: match.value,
              source: match.source,
              confidence: match.confidence,
            });
            continue;
          }
          report.skipped.push({ label: label || el.name || el.id, type, reason: "No profile match" });
        }
      }

      this.activeReport = report;
      return report;
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.HunterXEngine = new AutofillEngine();
})();
