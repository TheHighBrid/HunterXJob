import pytest

from app.apply_resolver import host_matches, is_ats_url, resolve_apply_target, unsafe_reason

JOB_BANK = "https://www.jobbank.gc.ca/jobsearch/jobposting/12345678"


def _page(body: str) -> str:
    return f"<html><body><h1>Fraud Analyst</h1>{body}</body></html>"


def test_job_bank_listing_resolves_to_direct_greenhouse_posting():
    html = _page(
        '<a href="/jobsearch/jobposting/999">Similar job</a>'
        '<a href="https://www.linkedin.com/company/example">LinkedIn</a>'
        '<a href="https://boards.greenhouse.io/exampleco/jobs/4012345">Apply on employer site</a>'
        '<a href="https://www.example.com/privacy">Privacy</a>'
    )
    res = resolve_apply_target(JOB_BANK, html)
    assert res.outcome == "ats"
    assert res.apply_url == "https://boards.greenhouse.io/exampleco/jobs/4012345"


def test_listing_already_on_ats_is_kept():
    url = "https://jobs.lever.co/exampleco/1b2c3d4e"
    res = resolve_apply_target(url)
    assert (res.outcome, res.apply_url) == ("ats", url)


@pytest.mark.parametrize(
    "url",
    ["https://www.linkedin.com/jobs/view/123", "https://ca.indeed.com/viewjob?jk=abc", "https://www.glassdoor.ca/job-listing/x"],
)
def test_unsupported_aggregators_are_never_targets(url):
    assert resolve_apply_target(url, _page('<a href="https://boards.greenhouse.io/x/jobs/1">Apply</a>')).outcome == "unsupported"


@pytest.mark.parametrize(
    "href,why",
    [
        ("https://boards.greenhouse.io.evil.example/jobs/1", "look-alike host"),
        ("https://evilgreenhouse.io/jobs/1", "suffix without dot"),
        ("http://boards.greenhouse.io/x/jobs/1", "plain http"),
        ("https://user:pw@boards.greenhouse.io/x/jobs/1", "credentials"),
        ("https://127.0.0.1/apply", "raw IP"),
        ("https://tracker.example.com/click?url=https://boards.greenhouse.io/x/jobs/1", "redirect wrapper"),
        ("javascript:alert(1)", "javascript scheme"),
        ("https://boards.greenhouse.io:8443/x/jobs/1", "odd port"),
    ],
)
def test_scraping_traps_never_become_apply_targets(href, why):
    res = resolve_apply_target(JOB_BANK, _page(f'<a href="{href}">Apply now</a>'))
    assert res.outcome == "manual", why
    assert res.apply_url == ""


def test_hidden_honeypot_ats_link_is_ignored():
    html = _page(
        '<a style="display: none" href="https://boards.greenhouse.io/trap/jobs/1">Apply</a>'
        '<a hidden href="https://jobs.lever.co/trap/2">Apply</a>'
        '<a aria-hidden="true" href="https://jobs.ashbyhq.com/trap/3">Apply</a>'
    )
    assert resolve_apply_target(JOB_BANK, html).outcome == "manual"


def test_links_inside_scripts_are_ignored():
    html = _page('<script>var a = "<a href=\\"https://boards.greenhouse.io/x/jobs/1\\">Apply</a>";</script>')
    assert resolve_apply_target(JOB_BANK, html).outcome == "manual"


def test_two_different_ats_hosts_go_to_review():
    html = _page(
        '<a href="https://boards.greenhouse.io/a/jobs/1">Apply</a><a href="https://jobs.lever.co/b/2">Apply</a>'
    )
    res = resolve_apply_target(JOB_BANK, html)
    assert res.outcome == "review"
    assert res.apply_url == ""
    assert len(res.candidates) == 2


def test_employer_careers_page_is_review_not_auto_target():
    res = resolve_apply_target(JOB_BANK, _page('<a href="https://www.example.com/careers/fraud-analyst">Apply</a>'))
    assert res.outcome == "review"
    assert res.apply_url == ""
    assert res.candidates == ("https://www.example.com/careers/fraud-analyst",)


def test_email_only_listing_and_noreply_filtered():
    html = _page('<p>Send your CV to noreply@example.com or</p><a href="mailto:jobs@example.com?subject=x">jobs@example.com</a>')
    res = resolve_apply_target(JOB_BANK, html)
    assert (res.outcome, res.apply_email) == ("email", "jobs@example.com")


def test_no_html_and_unsafe_listing():
    assert resolve_apply_target(JOB_BANK).outcome == "manual"
    assert resolve_apply_target("http://www.jobbank.gc.ca/x").outcome == "rejected"
    assert resolve_apply_target("").outcome == "rejected"


def test_resolution_is_deterministic_regardless_of_link_order():
    links = [
        '<a href="https://boards.greenhouse.io/x/jobs/2">Apply</a>',
        '<a href="https://boards.greenhouse.io/x/jobs/1">Apply</a>',
    ]
    a = resolve_apply_target(JOB_BANK, _page("".join(links)))
    b = resolve_apply_target(JOB_BANK, _page("".join(reversed(links))))
    assert a == b


def test_host_matching_and_unsafe_reason_helpers():
    assert host_matches("boards.greenhouse.io", ("greenhouse.io",))
    assert not host_matches("greenhouse.io.evil.example", ("greenhouse.io",))
    assert is_ats_url("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/1")
    assert not is_ats_url("https://xn--grenhouse-e8a.io/jobs/1")
    assert unsafe_reason("https://www.example.com/careers") is None
