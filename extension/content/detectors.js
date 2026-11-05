/**
 * HunterXJob Content Detectors
 * Identifies active job application platforms, foreground modal forms, and metadata.
 */
(() => {
  window.HunterXDetectors = {
    detectPlatform() {
      const host = window.location.hostname.toLowerCase();
      const path = window.location.pathname.toLowerCase();

      // 1. LinkedIn
      if (host.includes("linkedin.com")) {
        return {
          id: "linkedin",
          name: "LinkedIn Easy Apply",
          isJobPage: path.includes("/jobs/") || document.querySelector(".jobs-easy-apply-modal") !== null,
          rootSelector: ".jobs-easy-apply-modal, .jobs-apply-form, [data-view-name*='easy-apply'], form",
        };
      }

      // 2. Indeed
      if (host.includes("indeed.com") || host.includes("indeed.ca")) {
        return {
          id: "indeed",
          name: "Indeed Smart Apply",
          isJobPage: host.includes("apply") || path.includes("/viewjob") || document.querySelector("#ia-container, .ia-BasePage") !== null,
          rootSelector: "#ia-container, .ia-BasePage, div[data-testid='smart-apply-container'], form",
        };
      }

      // 3. Canadian Federal Government (GC Jobs / Emplois GC)
      if (host.includes("jobs.gc.ca") || host.includes("emploisfp-psjobs.cfp-psc.gc.ca") || host.includes("psjobs-emploisfp.cfp-psc.gc.ca") || (host.includes("canada.ca") && path.includes("jobs"))) {
        return {
          id: "gc_jobs",
          name: "Government of Canada (GC Jobs) 🇨🇦",
          isJobPage: true,
          rootSelector: "#frmApplication, .wb-frm, form[action*='process'], main, form",
        };
      }

      // 4. Ontario Public Service (OPS)
      if (host.includes("gojobs.gov.on.ca")) {
        return {
          id: "ops",
          name: "Ontario Public Service (OPS) 🇨🇦",
          isJobPage: true,
          rootSelector: "#applyForm, #jobQuestions, form",
        };
      }

      // 5. City of Ottawa / Ville de Gatineau / Canadian Municipal
      if (host.includes("cityofottawa.hua.hrsmart.com") || (host.includes("ottawa.ca") && path.includes("jobs")) || host.includes("gatineau.cvmanager.com") || (host.includes("gatineau.ca") && path.includes("emploi"))) {
        return {
          id: "ncr_municipal",
          name: host.includes("gatineau") ? "Ville de Gatineau (Municipal) 🇨🇦" : "City of Ottawa (Municipal) 🇨🇦",
          isJobPage: true,
          rootSelector: "#frmApply, #formApplication, form",
        };
      }

      // 6. Canada Job Bank / Guichet-Emplois / Provincial Banks
      if (host.includes("jobbank.gc.ca") || host.includes("workbc.ca") || host.includes("quebecemploi.gouv.qc.ca")) {
        return {
          id: "jobbank_ca",
          name: "Canada Job Bank / Provincial Portal 🇨🇦",
          isJobPage: true,
          rootSelector: "#job-apply-form, .apply-form, form",
        };
      }

      // 7. Workday
      if (host.includes("myworkdayjobs.com") || document.querySelector("[data-automation-id='applicationPage']") !== null) {
        return {
          id: "workday",
          name: "Workday Enterprise",
          isJobPage: true,
          rootSelector: "[data-automation-id='applicationPage'], [data-automation-id='formFieldContainer'], main, form",
        };
      }

      // 8. Ashby
      if (host.includes("ashbyhq.com") || document.querySelector("[data-field-path]") !== null) {
        return {
          id: "ashby",
          name: "Ashby HQ",
          isJobPage: true,
          rootSelector: "#ashby-application-form, form",
        };
      }

      // 9. Greenhouse
      if (host.includes("greenhouse.io") || document.querySelector("#application_form, form[id*='greenhouse']") !== null) {
        return {
          id: "greenhouse",
          name: "Greenhouse",
          isJobPage: true,
          rootSelector: "#application_form, form[id*='greenhouse'], form",
        };
      }

      // 10. Lever
      if (host.includes("lever.co") || document.querySelector(".application-form, #application-form") !== null) {
        return {
          id: "lever",
          name: "Lever",
          isJobPage: true,
          rootSelector: ".application-form, #application-form, form",
        };
      }

      // 11. SmartRecruiters
      if (host.includes("smartrecruiters.com") || document.querySelector("spl-application-form") !== null) {
        return {
          id: "smartrecruiters",
          name: "SmartRecruiters",
          isJobPage: true,
          rootSelector: "spl-application-form, form",
        };
      }

      // Fallback: Generic job application form
      const hasJobIndicators = /job|career|apply|application|candidate|recruit/i.test(document.title + " " + window.location.href);
      return {
        id: "generic",
        name: hasJobIndicators ? "Job Application Portal" : "Web Form",
        isJobPage: hasJobIndicators,
        rootSelector: "dialog[open], .modal.show, .modal-open, form, main, body",
      };
    },

    getForegroundContainer() {
      // Look for open dialogs / modal containers first (top priority for Indeed/LinkedIn)
      const openDialog = document.querySelector("dialog[open], [role='dialog']:not([aria-hidden='true']), .modal.show, .jobs-easy-apply-modal, #ia-container");
      if (openDialog && openDialog.offsetParent !== null) {
        return openDialog;
      }

      // Next look for the platform-specific root
      const platform = this.detectPlatform();
      if (platform.rootSelector) {
        const found = document.querySelector(platform.rootSelector);
        if (found) return found;
      }

      // Default to visible form or body
      const form = document.querySelector("form");
      return form || document.body;
    },

    extractJobMetadata() {
      let title = "";
      let company = "";

      const titleSelectors = [
        ".jobs-unified-top-card__job-title",
        ".jobsearch-JobInfoHeader-title",
        "[data-automation-id='jobPostingHeader']",
        "h1.job-title",
        "h1[class*='title']",
        "h1",
      ];
      for (const sel of titleSelectors) {
        const el = document.querySelector(sel);
        if (el && el.textContent.trim()) {
          title = el.textContent.trim();
          break;
        }
      }

      const companySelectors = [
        ".jobs-unified-top-card__company-name",
        "[data-company-name='true']",
        ".jobsearch-CompanyInfoContainer",
        "[data-automation-id='companyName']",
        ".company-name",
        "[class*='company']",
      ];
      for (const sel of companySelectors) {
        const el = document.querySelector(sel);
        if (el && el.textContent.trim()) {
          company = el.textContent.trim().split("\n")[0];
          break;
        }
      }

      return {
        title: title || document.title,
        company: company || window.location.hostname.replace(/^www\./, "").split(".")[0],
        url: window.location.href,
        platform: this.detectPlatform().id,
      };
    }
  };
})();
