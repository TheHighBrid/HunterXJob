"""SCAFFOLD ONLY — Upwork proposal-submission adapter. NOT implemented.

============================================================================
 WARNING: ToS RISK / ROADMAP ITEM — DO NOT ENABLE IN PRODUCTION WITHOUT
 UNDERSTANDING THE RISK.
============================================================================

Per docs/ARCHITECTURE.md section 2 ("The cautionary lesson") and section 3
("Explicitly roadmap / not fully built now"):

- Automated, unreviewed proposal submission on Upwork is against Upwork's
  Terms of Service, and Upwork proposals also cost "Connects" (real money)
  per submission — an extra reason unreviewed automation here is
  particularly risky.
- This class exists purely as an interface-shaped scaffold. `enabled`
  defaults to False, and the automation cycle in app/services/scheduler.py
  skips any adapter with enabled=False — this adapter does nothing unless a
  developer deliberately opts in.
- HunterXJob NEVER stores or handles your Upwork password. Any real
  implementation would require a Playwright storageState file (exported
  from your own interactive login) via PLAYWRIGHT_STORAGE_STATE_DIR.
- This scaffold explicitly does NOT and MUST NOT implement any
  stealth/anti-detection/CAPTCHA-bypass tooling. That class of technique
  exists to defeat a platform's anti-bot defenses and is out of scope for
  this project regardless of automation mode.
============================================================================
"""
from __future__ import annotations

from pathlib import Path

from app.services.automation.base import ApplicationAdapter, AutomationResult


class UpworkAdapter(ApplicationAdapter):
    """Roadmap scaffold for Upwork proposal submission. Inert by default (enabled=False)."""

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
                "UpworkAdapter is a roadmap scaffold and is disabled by default "
                "(enabled=False). See docs/ARCHITECTURE.md section 3 (roadmap) and "
                "section 2 ('The cautionary lesson') for why automated Upwork "
                "proposal submission is not built out in v1 (also note: Upwork "
                "proposals cost real Connects, an extra reason not to automate "
                "unreviewed). To experiment at your own risk: subclass or "
                "monkeypatch enabled=True, provide a Playwright storageState JSON "
                "file (from your own interactive login — this code never touches "
                "your password) via storage_state_path, and implement the actual "
                "proposal-form selector/flow logic yourself. This scaffold "
                "intentionally contains no stealth/anti-detection code and never "
                "will."
            )

        if not self.storage_state_path or not Path(self.storage_state_path).exists():
            raise NotImplementedError(
                "No Playwright storageState file configured/found at "
                f"{self.storage_state_path!r}. Generate one by logging into Upwork "
                "interactively yourself, then implement the proposal flow — this "
                "scaffold does not implement it. See docs/ARCHITECTURE.md roadmap "
                "section."
            )

        raise NotImplementedError(
            "Upwork proposal-submission automation is not implemented. This is "
            "an explicit roadmap item per docs/ARCHITECTURE.md — hand-tuned "
            "Upwork selectors need real-account testing that wasn't available "
            "when this repo was built. Implement the actual form-fill/submit "
            "flow here if you choose to take on the ToS risk yourself."
        )
