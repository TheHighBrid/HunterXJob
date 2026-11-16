import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from app.captcha_bridge import MockCaptchaSolver, detect_captcha, inject_captcha_token
from app.evidence import record_evidence
from app.models import Application, Job, PipelineStage
from app.submission_engine import execute_live_submission
from tests.profile_helpers import memory_db


def test_captcha_detection_and_token_injection():
    async def _test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # HTML with reCAPTCHA widget
            await page.set_content("""
            <html>
            <body>
              <form id="apply">
                <input name="email" value="test@example.com">
                <div class="g-recaptcha" data-sitekey="6Le-test-sitekey-xyz123"></div>
                <textarea name="g-recaptcha-response" id="g-recaptcha-response" style="display:none;"></textarea>
              </form>
            </body>
            </html>
            """)

            # 1. Detect CAPTCHA
            challenge = await detect_captcha(page)
            assert challenge.detected is True
            assert challenge.captcha_type == "recaptcha"
            assert challenge.sitekey == "6Le-test-sitekey-xyz123"
            assert challenge.resolved is False

            # 2. Solve and inject
            solver = MockCaptchaSolver(token="dummy-recaptcha-response-token-999")
            token = await solver.solve(challenge.captcha_type, challenge.sitekey, page.url)
            assert token == "dummy-recaptcha-response-token-999"

            injected = await inject_captcha_token(page, challenge, token)
            assert injected is True
            assert challenge.resolved is True

            # 3. Verify DOM has the token
            updated_challenge = await detect_captcha(page)
            assert updated_challenge.resolved is True
            assert updated_challenge.response_token == "dummy-recaptcha-response-token-999"

            await browser.close()

    asyncio.run(_test())


def test_live_submission_dry_run_prevention():
    async def _test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.set_content("""
            <html><body>
              <form id="apply">
                <input name="first_name" value="Alex">
                <button type="submit" id="submit_app">Submit Application</button>
              </form>
            </body></html>
            """)

            # When allow_live_submit=False, submit button is never clicked
            result = await execute_live_submission(page, allow_live_submit=False)
            assert result.status == "dry_run_prevented"
            assert result.success is False
            assert "locked" in (result.error or "")

            await browser.close()

    asyncio.run(_test())


def test_live_submission_blocked_by_unresolved_captcha():
    async def _test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.set_content("""
            <html><body>
              <form id="apply">
                <div class="g-recaptcha" data-sitekey="test-sitekey"></div>
                <textarea name="g-recaptcha-response"></textarea>
                <button type="submit" id="submit_app">Submit Application</button>
              </form>
            </body></html>
            """)

            # No solver provided: fails closed
            result = await execute_live_submission(page, allow_live_submit=True, captcha_solver=None)
            assert result.status == "blocked"
            assert result.success is False
            assert result.captcha_detected is True
            assert result.captcha_resolved is False

            await browser.close()

    asyncio.run(_test())


def test_live_submission_executes_and_verifies_confirmation(tmp_path):
    async def _test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            screenshot_path = tmp_path / "post_submit_proof.png"

            # Application page with submit handler that displays confirmation
            await page.set_content("""
            <html><body>
              <form id="job-app">
                <input name="full_name" value="Taylor Swift">
                <div class="g-recaptcha" data-sitekey="sitekey-123"></div>
                <textarea name="g-recaptcha-response"></textarea>
                <button type="submit" id="submit_app">Submit Application</button>
              </form>
              <script>
                document.getElementById('job-app').addEventListener('submit', function(e) {
                  e.preventDefault();
                  document.body.innerHTML = `
                    <div class="confirmation-container">
                      <h1>Thank you for applying!</h1>
                      <p>Your application has been submitted successfully to the recruiting team.</p>
                      <p class="ref-num">Reference ID: APP-2026-X78910</p>
                    </div>
                  `;
                });
              </script>
            </body></html>
            """)

            solver = MockCaptchaSolver(token="valid-token-777")

            result = await execute_live_submission(
                page,
                allow_live_submit=True,
                captcha_solver=solver,
                screenshot_path=screenshot_path,
            )

            assert result.status == "submitted"
            assert result.success is True
            assert result.captcha_detected is True
            assert result.captcha_resolved is True
            assert result.candidate_id == "APP-2026-X78910"
            assert "Thank you for applying" in (result.confirmation_text or "")
            assert screenshot_path.is_file()
            assert result.screenshot_sha256 is not None

            await browser.close()

    asyncio.run(_test())


def test_submission_evidence_ledger_integration():
    db = memory_db()
    job = Job(
        id="greenhouse:test:101",
        source="greenhouse",
        external_id="101",
        title="Lead Engineer",
        company="NovaCorp",
        url="https://boards.greenhouse.io/novacorp/jobs/101",
        stage=PipelineStage.ready_to_apply.value,
    )
    db.add(job)
    app = Application(id="app-101", job_id=job.id, stage=PipelineStage.ready_to_apply.value, adapter_name="greenhouse")
    db.add(app)
    db.commit()

    # Record real submission evidence
    evidence = record_evidence(
        db,
        app,
        kind="submission",
        confirmation_text="Thank you for applying! Your application has been submitted. Ref: APP-987654",
        candidate_id="APP-987654",
        final_url="https://boards.greenhouse.io/novacorp/jobs/101/confirmation",
        payload={"job_id": job.id, "applicant": "Test Applicant"},
    )

    assert evidence.kind == "submission"
    assert evidence.sufficient is True
    assert evidence.candidate_id == "APP-987654"
    assert app.stage == PipelineStage.submitted.value
