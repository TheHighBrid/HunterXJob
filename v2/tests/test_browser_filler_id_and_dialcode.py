"""Red-to-green tests for CSS-escaped id selectors and strict phone dial-code read-back.

These cover Wave 2 follow-up work on ``browser_filler``: route ``[id="..."]``
lookups through CSSOM-escaped selectors / ``getElementById``, and fail closed
when a phone dial-code read-back is the wrong country (including +44 vs a
Canadian number, and Canada vs US which share +1).
"""
from __future__ import annotations

import asyncio

import pytest

from app.browser_filler import (
    _fill_input_by_heuristics,
    _find_input_in_target,
    choose_location_option,
    css_escape,
    css_id_selector,
    id_attribute_selector,
    phone_dial_code_readback_is_valid,
    split_idrefs,
)


def _legacy_id_attribute_selector(element_id: str) -> str:
    """The pre-fix ``[id="{k}"]`` interpolation that breaks on quotes."""
    return f'[id="{element_id}"]'


def _legacy_phone_country_matched(read_back: str, val_str: str) -> bool:
    """The pre-fix phone_country matcher: any leading '+' counted as success."""
    rb_str = str(read_back or "").strip()
    return bool(rb_str) and (
        rb_str.startswith("+")
        or val_str.lower() in rb_str.lower()
        or rb_str.lower() in val_str.lower()
    )


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("question_123", "question_123"),
        ("question_123[]", "question_123\\[\\]"),
        (":r1:", "\\:r1\\:"),
        ("123abc", "\\31 23abc"),
        ('q"1\\', 'q\\"1\\\\'),
    ],
)
def test_css_escape_mirrors_cssom(raw, escaped):
    assert css_escape(raw) == escaped
    assert css_id_selector(raw) == "#" + escaped


def test_legacy_id_selector_breaks_on_embedded_quotes_new_helper_does_not():
    raw = 'q"x'
    assert _legacy_id_attribute_selector(raw) == '[id="q"x"]'
    assert id_attribute_selector(raw) == '[id="q\\"x"]'
    assert _legacy_id_attribute_selector(raw) != id_attribute_selector(raw)


def test_split_idrefs_splits_aria_controls_lists():
    assert split_idrefs(":lb1: extra") == [":lb1:", "extra"]
    assert split_idrefs(None) == []


def test_choose_location_option_fails_closed_on_us_lookalike():
    options = ["Ottawa, KS, USA", "Nottawa, ON, Canada", "Ottawa, ON, Canada"]
    assert choose_location_option("Ottawa, Ontario, Canada", options) == "Ottawa, ON, Canada"
    assert choose_location_option("Ottawa, Ontario, Canada", ["Ottawa, KS, USA", "Nottawa, ON, Canada"]) is None


def test_legacy_phone_country_matcher_accepts_wrong_dial_code_new_helper_rejects():
    assert _legacy_phone_country_matched("+44", "Canada") is True
    assert phone_dial_code_readback_is_valid(read_back="+44", expected="Canada") is False


def test_plus_forty_four_fails_for_canadian_phone_number():
    assert phone_dial_code_readback_is_valid(
        read_back="+44 20 7946 0958",
        expected="+1 613 555 0142",
        expected_country="Canada",
    ) is False


def test_canada_vs_us_requires_selected_country_and_fails_closed_when_ambiguous():
    canadian = "+1 613 555 0142"
    # Same +1 dial code, wrong country label.
    assert phone_dial_code_readback_is_valid(
        read_back=canadian,
        expected=canadian,
        selected_country="United States",
        expected_country="Canada",
    ) is False
    # Bare +1 cannot distinguish CA from US when Canada is expected.
    assert phone_dial_code_readback_is_valid(
        read_back="+1",
        expected="Canada",
    ) is False
    # Explicit Canada label is accepted.
    assert phone_dial_code_readback_is_valid(
        read_back="Canada (+1)",
        expected="Canada",
        selected_country="Canada",
        expected_country="Canada",
    ) is True
    # Matching number with an explicit Canadian country hint is accepted.
    assert phone_dial_code_readback_is_valid(
        read_back=canadian,
        expected=canadian,
        selected_country="Canada",
        expected_country="Canada",
    ) is True
    # The legacy matcher treated a bare +1 as success for "Canada".
    assert _legacy_phone_country_matched("+1", "Canada") is True


async def _with_page(html: str):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return pw, browser, page


def _run(coro):
    return asyncio.run(coro)


def test_find_input_by_quoted_and_css_special_ids():
    html = """
    <form>
      <input id='q&quot;x' name='quoted' type="text" />
      <input id="question_123[]" name="family" type="text" />
      <input id=":r1:" name="react" type="text" />
    </form>
    """

    async def _inner():
        pw, browser, page = await _with_page(html)
        try:
            quoted, sel = await _find_input_in_target(page, 'q"x')
            assert quoted is not None, "quoted id must be locatable"
            assert "q" in sel
            family, _sel = await _find_input_in_target(page, "question_123[]")
            assert family is not None
            react, _sel = await _find_input_in_target(page, ":r1:")
            assert react is not None

            # The unescaped legacy selector is invalid CSS and must not resolve the quoted id.
            from playwright.async_api import Error as PlaywrightError
            with pytest.raises(PlaywrightError):
                await page.locator(_legacy_id_attribute_selector('q"x')).count()
        finally:
            await browser.close()
            await pw.stop()

    _run(_inner())


def test_location_listbox_resolves_css_special_aria_controls_ids():
    html = """
    <form>
      <input id="location" role="combobox" class="autocomplete" aria-controls=":lb1: extra" />
      <div id=":lb1:" role="listbox">
        <div role="option" onclick="document.getElementById('location').value = this.textContent.trim()">Ottawa, KS, USA</div>
        <div role="option" onclick="document.getElementById('location').value = this.textContent.trim()">Ottawa, ON, Canada</div>
      </div>
    </form>
    """

    async def _inner():
        pw, browser, page = await _with_page(html)
        try:
            # Old interpolation looked up the literal id ":lb1: extra" and found nothing.
            assert await page.locator(_legacy_id_attribute_selector(":lb1: extra")).count() == 0
            report = await _fill_input_by_heuristics(
                page, "location", "Ottawa, Ontario, Canada", label="Location",
            )
            assert report.matched is True
            assert "Ottawa, ON, Canada" in (report.read_back_value or "")
            assert "KS" not in (report.read_back_value or "")
        finally:
            await browser.close()
            await pw.stop()

    _run(_inner())


def test_phone_country_readback_rejects_plus_44_and_ambiguous_plus_one():
    plus44 = """
    <form>
      <input id="phone_country" name="phone_country" value="+44" />
      <script>
        const el = document.getElementById("phone_country");
        const lock = () => { el.value = "+44"; };
        el.addEventListener("input", lock);
        el.addEventListener("change", lock);
      </script>
    </form>
    """
    plus1 = """
    <form>
      <input id="phone_country" name="phone_country" value="+1" />
      <script>
        const el = document.getElementById("phone_country");
        const lock = () => { el.value = "+1"; };
        el.addEventListener("input", lock);
        el.addEventListener("change", lock);
      </script>
    </form>
    """
    canada = """
    <form>
      <select id="phone_country" name="phone_country">
        <option value="United States">United States (+1)</option>
        <option value="Canada" selected>Canada (+1)</option>
        <option value="United Kingdom">United Kingdom (+44)</option>
      </select>
    </form>
    """

    async def _inner():
        pw, browser, page = await _with_page(plus44)
        try:
            report = await _fill_input_by_heuristics(page, "phone_country", "Canada")
            assert report.read_back_value == "+44"
            assert report.matched is False
            # Document the old bug: a leading '+' used to satisfy the matcher.
            assert _legacy_phone_country_matched(str(report.read_back_value), "Canada") is True
        finally:
            await browser.close()
            await pw.stop()

        pw, browser, page = await _with_page(plus1)
        try:
            report = await _fill_input_by_heuristics(page, "phone_country", "Canada")
            assert report.read_back_value == "+1"
            assert report.matched is False
        finally:
            await browser.close()
            await pw.stop()

        pw, browser, page = await _with_page(canada)
        try:
            report = await _fill_input_by_heuristics(page, "phone_country", "Canada")
            assert report.matched is True
        finally:
            await browser.close()
            await pw.stop()

    _run(_inner())


def test_phone_number_readback_fails_when_country_is_us_but_expected_is_canada():
    html = """
    <form>
      <select id="country" name="phone_country">
        <option value="United States" selected>United States (+1)</option>
        <option value="Canada">Canada (+1)</option>
      </select>
      <input id="phone" name="phone" type="tel" />
    </form>
    """

    async def _inner():
        pw, browser, page = await _with_page(html)
        try:
            answers = {"phone": "+1 613 555 0142", "phone_country": "Canada"}
            report = await _fill_input_by_heuristics(
                page, "phone", "+1 613 555 0142", answers=answers,
            )
            # Number digits match, but the selected country widget is US.
            assert report.matched is False
        finally:
            await browser.close()
            await pw.stop()

    _run(_inner())
