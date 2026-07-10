"""SCAFFOLD ONLY — LinkedIn Easy Apply adapter. NOT implemented.

============================================================================
 WARNING: ToS RISK / ROADMAP ITEM — DO NOT ENABLE IN PRODUCTION WITHOUT
 UNDERSTANDING THE RISK.
============================================================================

Per docs/ARCHITECTURE.md section 2 ("The cautionary lesson") and section 3
("Explicitly roadmap / not fully built now"):

- Automated, unreviewed application submission to LinkedIn is against
  LinkedIn's Terms of Service. Projects that did exactly this
  (Jobs_Applier_AI_Agent_AIHawk, darsan-in/Job-Hunter) are both archived —
  almost certainly because of ToS enforcement action. Using this adapter is
  entirely at your own risk and own account.
- This class exists as a class-implementing-the-interface scaffold so the
  rest of the system (scheduler, automation_cycle, safety rails) has a
  consistent shape to call into if/when someone chooses to build this out
  for their own use, with their own testing, against their own account.
- `enabled` defaults to False. The automation cycle in
  app/services/scheduler.py skips any adapter with enabled=False, so this
  adapter is inert unless a developer deliberately flips the flag AND wires
  up a storageState file.
- HunterXJob NEVER stores or handles your LinkedIn password. If you choose
  to build this out, you would log in once yourself in a real, interactive
  browser session and export Playwright's storageState (cookies + local
  storage) to the JSON file path configured via
  PLAYWRIGHT_STORAGE_STATE_DIR — the adapter would load that state to
  resume your already-authenticated session, never touching credentials.
- This scaffold explicitly does NOT and MUST NOT implement any form of
  stealth/anti-detection/CAPTCHA-bypass tooling (undetected-chromedriver,
  CAPTCHA-solving services, fingerprint spoofing, etc). That category of
  technique exists specifically to defeat a platform's anti-bot defenses,
  and is out of scope for this project regardless of automation mode.
============================================================================
"""
from __future__ import annotations

from pathlib import Path

from app.services.automation.base import ApplicationAdapter, AutomationResult


class LinkedInAdapter(ApplicationAdapter):
    """Roadmap scaffold for LinkedIn Easy Apply. Inert by default (enabled=False)."""

    enabled: bool = False

    def __init__(self, storage_state_path: str | None = None):
        self.storage_state_path = storage_state_path

    def submit(
        self,
        application,
        job_posting,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None = None,
    ) -> AutomationResult:
        if not self.enabled:
            raise NotImplementedError(
                "LinkedInAdapter is a roadmap scaffold and is disabled by default "
                "(enabled=False). See docs/ARCHITECTURE.md section 3 (roadmap) and "
                "section 2 ('The cautionary lesson') for why automated LinkedIn "
                "submission is not built out in v1. To experiment at your own risk: "
                "subclass or monkeypatch enabled=True, provide a Playwright "
                "storageState JSON file (from your own interactive login — this "
                "code never touches your password) via storage_state_path, and "
                "implement the actual Easy Apply selector/flow logic yourself. "
                "This scaffold intentionally contains no stealth/anti-detection "
                "code and never will."
            )

        if not self.storage_state_path or not Path(self.storage_state_path).exists():
            raise NotImplementedError(
                "No Playwright storageState file configured/found at "
                f"{self.storage_state_path!r}. Generate one by logging into "
                "LinkedIn interactively yourself, then implement the Easy Apply "
                "flow — this scaffold does not implement it. See "
                "docs/ARCHITECTURE.md roadmap section."
            )

        raise NotImplementedError(
            "LinkedIn Easy Apply automation is not implemented. This is an "
            "explicit roadmap item per docs/ARCHITECTURE.md — hand-tuned "
            "LinkedIn selectors need real-account testing that wasn't available "
            "when this repo was built. Implement the actual form-fill/submit "
            "flow here if you choose to take on the ToS risk yourself."
        )
