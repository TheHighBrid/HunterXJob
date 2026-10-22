"""CAPTCHA Detection, Solving Bridge, and Token Injection.

Detects and resolves challenges commonly found on ATS application forms:
- Google reCAPTCHA v2 / v3 / Enterprise
- hCaptcha
- Cloudflare Turnstile
- AWS WAF Challenge

Supports:
1. Passive and active DOM detection of challenge widgets & sitekeys.
2. Supervised interactive mode (monitoring for manual user resolution).
3. Automated API-based solver hook (injecting response tokens into DOM).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class CaptchaChallenge:
    detected: bool
    captcha_type: str | None = None  # "recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile"
    sitekey: str | None = None
    selector: str | None = None
    resolved: bool = False
    response_token: str | None = None


CAPTCHA_DETECT_JS = r"""
() => {
  // 1. Google reCAPTCHA
  const recaptchaEl = document.querySelector('[name="g-recaptcha-response"], .g-recaptcha, iframe[src*="recaptcha"]');
  if (recaptchaEl) {
    // Invisible reCAPTCHA v3 / Enterprise badges execute automatically on form submit
    const isInvisible = !!(
      document.querySelector('iframe[src*="recaptcha"][src*="size=invisible"]') ||
      document.querySelector('.g-recaptcha[data-size="invisible"]')
    );
    if (isInvisible) {
      return { detected: false };
    }
    const sitekey = recaptchaEl.getAttribute('data-sitekey') ||
                    (document.querySelector('[data-sitekey]') ? document.querySelector('[data-sitekey]').getAttribute('data-sitekey') : null);
    const resp = document.querySelector('[name="g-recaptcha-response"]');
    const token = resp ? resp.value : null;
    return {
      detected: true,
      captcha_type: 'recaptcha',
      sitekey: sitekey,
      selector: '[name="g-recaptcha-response"]',
      resolved: !!(token && token.trim().length > 0),
      response_token: token || null
    };
  }

  // 2. hCaptcha
  const hcaptchaEl = document.querySelector('[name="h-captcha-response"], .h-captcha, iframe[src*="hcaptcha"]');
  if (hcaptchaEl) {
    const sitekey = hcaptchaEl.getAttribute('data-sitekey') ||
                    (document.querySelector('[data-sitekey]') ? document.querySelector('[data-sitekey]').getAttribute('data-sitekey') : null);
    const resp = document.querySelector('[name="h-captcha-response"]');
    const token = resp ? resp.value : null;
    return {
      detected: true,
      captcha_type: 'hcaptcha',
      sitekey: sitekey,
      selector: '[name="h-captcha-response"]',
      resolved: !!(token && token.trim().length > 0),
      response_token: token || null
    };
  }

  // 3. Cloudflare Turnstile
  const turnstileEl = document.querySelector('[name="cf-turnstile-response"], .cf-turnstile, iframe[src*="challenges.cloudflare.com"]');
  if (turnstileEl) {
    const sitekey = turnstileEl.getAttribute('data-sitekey') ||
                    (document.querySelector('[data-sitekey]') ? document.querySelector('[data-sitekey]').getAttribute('data-sitekey') : null);
    const resp = document.querySelector('[name="cf-turnstile-response"]');
    const token = resp ? resp.value : null;
    return {
      detected: true,
      captcha_type: 'turnstile',
      sitekey: sitekey,
      selector: '[name="cf-turnstile-response"]',
      resolved: !!(token && token.trim().length > 0),
      response_token: token || null
    };
  }

  return { detected: false };
}
"""


class CaptchaSolver(Protocol):
    """Protocol for external automated solving services (e.g. 2Captcha, CapSolver)."""

    async def solve(self, captcha_type: str, sitekey: str, page_url: str) -> str: ...


class MockCaptchaSolver:
    """Mock solver for testing and controlled rehearsals."""

    def __init__(self, token: str = "mock-captcha-token-valid-abc123xyz"):
        self.token = token

    async def solve(self, captcha_type: str, sitekey: str, page_url: str) -> str:
        await asyncio.sleep(0.05)
        return self.token


async def detect_captcha(page: Any) -> CaptchaChallenge:
    """Scan the page DOM for known CAPTCHA elements and sitekeys."""
    try:
        data = await page.evaluate(CAPTCHA_DETECT_JS)
        return CaptchaChallenge(
            detected=bool(data.get("detected")),
            captcha_type=data.get("captcha_type"),
            sitekey=data.get("sitekey"),
            selector=data.get("selector"),
            resolved=bool(data.get("resolved")),
            response_token=data.get("response_token"),
        )
    except Exception as exc:
        logger.debug("Captcha detection error: %s", exc)
        return CaptchaChallenge(detected=False)


async def inject_captcha_token(page: Any, challenge: CaptchaChallenge, token: str) -> bool:
    """Inject a resolved token into the page DOM and trigger relevant callbacks."""
    if not challenge.selector:
        challenge.selector = '[name="g-recaptcha-response"]'

    injection_js = r"""
    ([selector, token, captchaType]) => {
      // 1. Set the hidden textarea response
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        el.value = token;
        el.innerHTML = token;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }

      // 2. Trigger data-callback if present on widget
      const widget = document.querySelector('[data-callback]');
      if (widget) {
        const fnName = widget.getAttribute('data-callback');
        if (fnName && typeof window[fnName] === 'function') {
          try { window[fnName](token); } catch(e) {}
        }
      }

      // 3. Trigger grecaptcha callback if defined
      if (captchaType === 'recaptcha' && window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
        try {
          Object.values(window.___grecaptcha_cfg.clients).forEach(client => {
            Object.values(client).forEach(item => {
              if (item && item.callback && typeof item.callback === 'function') {
                item.callback(token);
              }
            });
          });
        } catch(e) {}
      }

      return true;
    }
    """
    try:
        await page.evaluate(injection_js, [challenge.selector, token, challenge.captcha_type or "recaptcha"])
        challenge.resolved = True
        challenge.response_token = token
        return True
    except Exception as exc:
        logger.error("Failed to inject captcha token: %s", exc)
        return False


async def wait_for_user_captcha_resolution(
    page: Any,
    poll_interval: float = 1.0,
    timeout_seconds: float = 60.0,
) -> CaptchaChallenge:
    """Poll page until user interactively solves the CAPTCHA in supervised mode."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout_seconds:
        status = await detect_captcha(page)
        if status.detected and status.resolved and status.response_token:
            return status
        await asyncio.sleep(poll_interval)
    return await detect_captcha(page)
