"""Wave 2 ATS DOM widgets: CSS-special ARIA ids, delayed location autocomplete, Ashby system fields.

Everything here is offline. The browser tests load inline HTML with ``page.set_content``
into a Playwright-owned headless Chromium; nothing is fetched and nothing is submitted.
"""
from __future__ import annotations

import asyncio

import pytest
from conftest import load_ats_json

from app.ashby_form import AshbyJobRef, ashby_system_field_key, parse_ashby_payload
from app.greenhouse_form import (
    ELEMENT_BY_ID_JS,
    LocationFillResult,
    choose_location_option,
    css_escape,
    css_id_selector,
    fill_greenhouse_location,
    id_attribute_selector,
    resolve_element_by_id,
    split_idrefs,
)

ASHBY = AshbyJobRef("northwind", "00000000-0000-4000-8000-000000000000")


# --- (1) CSS-special ARIA id bypass ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("question_123", "question_123"),
        ("question_123[]", "question_123\\[\\]"),
        ("react-select-3-listbox", "react-select-3-listbox"),
        ("a:b.c", "a\\:b\\.c"),
        ("123abc", "\\31 23abc"),
        ("-1x", "-\\31 x"),
        ("-", "\\-"),
        ("a b", "a\\ b"),
        ('q"1\\', 'q\\"1\\\\'),
        ("\x00x", "\ufffdx"),
        ("tab\tx", "tab\\9 x"),
        ("é_ok", "é_ok"),
        (":r1:", "\\:r1\\:"),
    ],
)
def test_css_escape_mirrors_cssom_escape(raw, escaped):
    assert css_escape(raw) == escaped
    assert css_id_selector(raw) == "#" + escaped


def test_id_attribute_selector_escapes_quotes_and_backslashes():
    assert id_attribute_selector('question_1[]') == '[id="question_1[]"]'
    assert id_attribute_selector('q"x\\y') == '[id="q\\"x\\\\y"]'


def test_css_id_selector_rejects_empty_ids():
    with pytest.raises(ValueError):
        css_id_selector("")


def test_split_idrefs_handles_whitespace_lists():
    assert split_idrefs("  label_question_1[]  hint:2\n") == ["label_question_1[]", "hint:2"]
    assert split_idrefs(None) == []


def test_element_by_id_js_uses_get_element_by_id():
    assert "document.getElementById" in ELEMENT_BY_ID_JS
    assert "querySelector" not in ELEMENT_BY_ID_JS


# --- (2) delayed location autocomplete: pure option choice ------------------------------------------

def test_location_choice_prefers_exact_and_province_abbreviation():
    options = ["Ottawa, KS, USA", "Nottawa, ON, Canada", "Ottawa, ON, Canada"]
    assert choose_location_option("Ottawa, Ontario, Canada", options) == "Ottawa, ON, Canada"


def test_location_choice_fails_closed_when_ambiguous():
    assert choose_location_option("Ottawa", ["Ottawa, ON, Canada", "Ottawa, IL, USA"]) is None


def test_location_choice_rejects_substring_city_and_foreign_country():
    assert choose_location_option("Ottawa, Ontario, Canada", ["Nottawa, ON, Canada", "Ottawa, KS, USA"]) is None
    assert choose_location_option("Gatineau, Quebec, Canada", ["Gatineau, QC, Canada"]) == "Gatineau, QC, Canada"


def test_location_choice_handles_accents_and_empty_inputs():
    assert choose_location_option("Montreal, QC", ["Montréal, Québec, Canada"]) == "Montréal, Québec, Canada"
    assert choose_location_option("", ["Ottawa, ON, Canada"]) is None
    assert choose_location_option("Ottawa", []) is None


# --- fakes for deterministic polling ---------------------------------------------------------------

class _FakeOption:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text

    async def inner_text(self):
        return self.text

    async def get_attribute(self, name):
        return None

    async def click(self):
        self.widget.value = self.text


class _FakeListbox:
    def __init__(self, widget):
        self.widget = widget

    async def query_selector_all(self, selector):
        assert selector == '[role="option"]'
        return [_FakeOption(self.widget, text) for text in self.widget.visible_options()]


class _FakeHandle:
    def __init__(self, element):
        self.element = element

    def as_element(self):
        return self.element


class _FakeCombobox:
    def __init__(self, widget):
        self.widget = widget

    async def get_attribute(self, name):
        return self.widget.controls_id if name == "aria-controls" else None

    async def click(self):
        return None

    async def fill(self, value):
        self.widget.value = value
        self.widget.typed = ""

    async def press_sequentially(self, text, delay=None):
        self.widget.delays.append(delay)
        self.widget.typed = text
        self.widget.value = text
        self.widget.polls_since_type = 0

    async def evaluate(self, script):
        return self.widget.value


class _FakePage:
    def __init__(self, widget):
        self.widget = widget

    async def evaluate_handle(self, script, element_id):
        assert script == ELEMENT_BY_ID_JS
        self.widget.lookups.append(element_id)
        return _FakeHandle(_FakeListbox(self.widget) if element_id == self.widget.controls_id else None)

    async def query_selector_all(self, selector):
        return []


class _Widget:
    """Options appear only after ``ready_after`` polls, like Greenhouse's debounced geocoder."""

    def __init__(self, options, *, ready_after=3, controls_id="question_9[]-listbox", answers_for=None):
        self.options, self.ready_after, self.controls_id = options, ready_after, controls_id
        self.answers_for = answers_for
        self.value, self.typed, self.polls_since_type = "", "", 0
        self.delays, self.lookups = [], []

    def visible_options(self):
        if not self.typed:
            return []
        self.polls_since_type += 1
        if self.polls_since_type <= self.ready_after:
            return []
        if self.answers_for is not None and self.typed not in self.answers_for:
            return []
        return list(self.options)


def _run(widget, answer, **kwargs):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    page = _FakePage(widget)
    result = asyncio.run(fill_greenhouse_location(page, _FakeCombobox(widget), answer, sleep=fake_sleep, **kwargs))
    return result, sleeps


def test_delayed_location_types_with_25ms_and_polls_until_options_arrive():
    widget = _Widget(["Ottawa, KS, USA", "Ottawa, ON, Canada"], ready_after=3)
    result, sleeps = _run(widget, "Ottawa, Ontario, Canada", poll_interval_ms=40, poll_attempts=6)
    assert isinstance(result, LocationFillResult)
    assert (result.status, result.selected, result.readback) == ("filled", "Ottawa, ON, Canada", "Ottawa, ON, Canada")
    assert widget.delays == [25]
    assert sleeps == [0.04] * 3
    assert set(widget.lookups) == {"question_9[]-listbox"}


def test_delayed_location_fails_closed_when_options_never_arrive():
    widget = _Widget(["Ottawa, ON, Canada"], ready_after=99)
    result, sleeps = _run(widget, "Ottawa, Ontario, Canada", poll_interval_ms=10, poll_attempts=4)
    assert (result.status, result.reason, result.selected) == ("review", "location_no_options", None)
    assert len(sleeps) == 8  # full query + city-prefix retry, each bounded at poll_attempts
    assert widget.value != "Ottawa, ON, Canada"


def test_delayed_location_retries_with_city_prefix():
    widget = _Widget(["Ottawa, ON, Canada"], ready_after=1, answers_for={"Ottawa"})
    result, _ = _run(widget, "Ottawa, Ontario, Canada", poll_interval_ms=10, poll_attempts=3)
    assert (result.status, result.selected, result.query) == ("filled", "Ottawa, ON, Canada", "Ottawa")
    assert widget.delays == [25, 25]


def test_delayed_location_fails_closed_on_ambiguous_options():
    widget = _Widget(["Ottawa, ON, Canada", "Ottawa, IL, USA"], ready_after=0)
    result, _ = _run(widget, "Ottawa", poll_interval_ms=10, poll_attempts=2)
    assert (result.status, result.reason) == ("review", "location_no_confident_match")
    assert result.options == ["Ottawa, ON, Canada", "Ottawa, IL, USA"]
    assert widget.value == ""  # the half-typed query is cleared, nothing is left selected


def test_delayed_location_fails_closed_on_readback_mismatch():
    widget = _Widget(["Ottawa, ON, Canada"], ready_after=0)

    class _StickyOption(_FakeOption):
        async def click(self):
            self.widget.value = "Toronto, ON, Canada"

    class _StickyListbox(_FakeListbox):
        async def query_selector_all(self, selector):
            return [_StickyOption(self.widget, text) for text in self.widget.visible_options()]

    page = _FakePage(widget)

    async def evaluate_handle(script, element_id):
        return _FakeHandle(_StickyListbox(widget))

    page.evaluate_handle = evaluate_handle

    async def no_sleep(_):
        return None

    result = asyncio.run(fill_greenhouse_location(page, _FakeCombobox(widget), "Ottawa, Ontario, Canada", sleep=no_sleep))
    assert (result.status, result.reason) == ("review", "location_readback_mismatch")


def test_delayed_location_rejects_empty_answer_and_bad_bounds():
    widget = _Widget(["Ottawa, ON, Canada"])
    result, sleeps = _run(widget, "  ")
    assert (result.status, result.reason, sleeps) == ("review", "location_no_answer", [])
    with pytest.raises(ValueError):
        _run(widget, "Ottawa", poll_attempts=0)


# --- (1)+(2) in a real offline Chromium -----------------------------------------------------------

_DELAYED_WIDGET_HTML = """
<label id="label_question_77[]" for="question_77[]">Location (City)</label>
<input id="question_77[]" role="combobox" aria-autocomplete="list" aria-expanded="false"
       aria-labelledby="label_question_77[]" aria-controls="react-select-question_77[]-listbox" />
<div id="react-select-question_77[]-listbox" role="listbox"></div>
<script>
  const input = document.getElementById('question_77[]');
  const box = document.getElementById('react-select-question_77[]-listbox');
  const places = ['Ottawa, KS, USA', 'Nottawa, ON, Canada', 'Ottawa, ON, Canada'];
  let timer = null;
  window.keyTimes = [];
  input.addEventListener('keydown', (e) => { if (e.key.length === 1) window.keyTimes.push(performance.now()); });
  input.addEventListener('input', () => {
    box.innerHTML = '';
    clearTimeout(timer);
    const q = input.value.toLowerCase();
    timer = setTimeout(() => {
      for (const p of places.filter((x) => x.toLowerCase().includes(q.split(',')[0]))) {
        const o = document.createElement('div');
        o.setAttribute('role', 'option');
        o.textContent = p;
        o.addEventListener('click', () => { input.value = p; box.innerHTML = ''; });
        box.appendChild(o);
      }
    }, 400);
  });
</script>
"""


def _with_page(html, body):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(html)
                return await body(page)
            finally:
                await browser.close()

    return asyncio.run(run())


def test_raw_hash_selector_breaks_but_bypass_resolves_special_ids_in_chromium():
    async def body(page):
        raw_error = None
        try:
            await page.query_selector("#question_77[]")
        except Exception as exc:  # Playwright surfaces the DOMException as an Error
            raw_error = str(exc)
        escaped = await page.query_selector(css_id_selector("question_77[]"))
        by_attr = await page.query_selector(id_attribute_selector("react-select-question_77[]-listbox"))
        by_id = await resolve_element_by_id(page, "react-select-question_77[]-listbox")
        missing = await resolve_element_by_id(page, "does-not-exist[]")
        label_ids = split_idrefs(await page.get_attribute(css_id_selector("question_77[]"), "aria-labelledby"))
        label = await resolve_element_by_id(page, label_ids[0])
        return raw_error, escaped, by_attr, by_id, missing, await label.inner_text()

    raw_error, escaped, by_attr, by_id, missing, label_text = _with_page(_DELAYED_WIDGET_HTML, body)
    assert raw_error is not None
    assert escaped is not None and by_attr is not None and by_id is not None
    assert missing is None
    assert label_text == "Location (City)"


def test_delayed_location_autocomplete_in_chromium():
    async def body(page):
        combobox = page.locator(css_id_selector("question_77[]"))
        result = await fill_greenhouse_location(page, combobox, "Ottawa, Ontario, Canada",
                                                poll_interval_ms=100, poll_attempts=20)
        key_times = await page.evaluate("window.keyTimes")
        return result, await combobox.input_value(), key_times

    result, value, key_times = _with_page(_DELAYED_WIDGET_HTML, body)
    assert (result.status, result.selected) == ("filled", "Ottawa, ON, Canada")
    assert value == "Ottawa, ON, Canada"
    assert len(key_times) == len("Ottawa, Ontario, Canada")  # one keydown per character, not pasted
    gaps = sorted(b - a for a, b in zip(key_times, key_times[1:]))
    assert gaps[len(gaps) // 2] >= 15  # median inter-key gap reflects the 25ms per-key delay


# --- (4) Ashby system field aliases -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("path", "title", "raw_type", "expected"),
    [
        ("_systemfield_name", "Name", "String", "full_name"),
        ("_systemfield_first_name", "First name", "String", "first_name"),
        ("_systemfield_last_name", "Last name", "String", "last_name"),
        ("_systemfield_preferred_name", "Preferred name", "String", "preferred_name"),
        ("_systemfield_email", "Email", "Email", "email"),
        ("_systemfield_phone", "Phone", "Phone", "phone"),
        ("_systemfield_resume", "Resume", "File", "resume"),
        ("_systemfield_cover_letter", "Cover letter", "File", "cover_letter"),
        ("_systemfield_location", "Location", "Location", "location"),
        ("_systemfield_linkedin", "LinkedIn", "SocialLink", "linkedin_url"),
        ("_systemfield_github", "GitHub", "SocialLink", "github_url"),
        ("_systemfield_website", "Website", "Url", "website_url"),
        ("_systemfield_portfolio", "Portfolio", "Url", "website_url"),
        ("_systemfield_twitter", "X", "SocialLink", "twitter_url"),
        ("_SystemField_Email", "Email", "Email", "email"),
        ("currentCompany", "Current company", "String", "current_company"),
        ("8768ddbb-009d-5659-b919-d358b49f45d5", "X", "String", "twitter_url"),
        ("8768ddbb-009d-5659-b919-d358b49f45d6", "X (formerly Twitter)", "String", "twitter_url"),
        ("aaaaaaaa-009d-5659-b919-d358b49f45d5", "Name", "String", "full_name"),
        ("bbbbbbbb-009d-5659-b919-d358b49f45d5", "First Name", "String", "first_name"),
        ("cccccccc-009d-5659-b919-d358b49f45d5", "Phone number", "Phone", "phone"),
    ],
)
def test_ashby_system_field_aliases(path, title, raw_type, expected):
    assert ashby_system_field_key(path, title, raw_type) == expected


@pytest.mark.parametrize(
    ("path", "title", "raw_type"),
    [
        ("_systemfield_recording_consent", "Interview Recording Consent", "ValueSelect"),
        ("_systemfield_unknown_future_field", "Name", "String"),
        ("dddddddd-009d-5659-b919-d358b49f45d5", "Company name", "String"),
        ("eeeeeeee-009d-5659-b919-d358b49f45d5", "Reference name", "String"),
        ("ffffffff-009d-5659-b919-d358b49f45d5", "Name", "ValueSelect"),
    ],
)
def test_ashby_unknown_system_fields_and_lookalikes_do_not_alias(path, title, raw_type):
    assert ashby_system_field_key(path, title, raw_type) is None


def _ashby_payload(fields):
    entries = [{"id": f"e{i}", "isRequired": True, "field": field} for i, field in enumerate(fields)]
    return {"jobPosting": {"id": ASHBY.posting_id, "title": "Analyst", "isListed": True,
                           "applicationForm": {"id": "f", "sections": [{"title": "", "fieldEntries": entries}]},
                           "surveyForms": []}}


def test_ashby_parse_maps_split_name_and_social_system_fields():
    form = parse_ashby_payload(_ashby_payload([
        {"path": "_systemfield_first_name", "title": "First name", "type": "String"},
        {"path": "_systemfield_last_name", "title": "Last name", "type": "String"},
        {"path": "_systemfield_linkedin", "title": "LinkedIn", "type": "SocialLink"},
        {"path": "_systemfield_website", "title": "Website", "type": "Url"},
        {"path": "_systemfield_recording_consent", "title": "Interview Recording Consent", "type": "ValueSelect",
         "selectableValues": [{"label": "I consent", "value": "yes"}, {"label": "I do not consent", "value": "no"}]},
    ]), ASHBY)
    keys = {control.key: control.vault_keys for control in form.controls}
    assert keys["_systemfield_first_name"] == ["_systemfield_first_name", "first_name"]
    assert keys["_systemfield_last_name"] == ["_systemfield_last_name", "last_name"]
    assert keys["_systemfield_linkedin"] == ["_systemfield_linkedin", "linkedin_url"]
    assert keys["_systemfield_website"] == ["_systemfield_website", "website_url"]
    assert keys["_systemfield_recording_consent"] == ["_systemfield_recording_consent"]


def test_ashby_engineer_fixture_maps_x_handle_to_twitter():
    form = parse_ashby_payload(load_ats_json("ashby_form_engineer.json")["data"], ASHBY)
    by_label = {control.label: control.vault_keys for control in form.controls}
    assert by_label["X"][1:] == ["twitter_url"]
    assert by_label["Name"][1:] == ["full_name"]
