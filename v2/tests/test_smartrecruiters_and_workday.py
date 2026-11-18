import types
import pytest
from app.adapter_runtime import ADAPTER_CATALOG, REGISTRY, SmartRecruitersAdapter, WorkdayAdapter
from app.browser_filler import fill_application_form
from app.form_engine import ControlType, FillPlan, FillPlanItem, FormControl
from app.models import AdapterMaturity
from app.real_forms import LiveFormProvider
from app.smartrecruiters_form import (
    SmartRecruitersJobRef,
    fetch_smartrecruiters_form,
    parse_smartrecruiters_form,
    parse_smartrecruiters_ref,
    smartrecruiters_ref_for_job,
)
from app.workday_form import (
    WorkdayJobRef,
    fetch_workday_form,
    parse_workday_form,
    parse_workday_ref,
    workday_ref_for_job,
)


def test_smartrecruiters_ref_parsing():
    url = "https://jobs.smartrecruiters.com/AcmeCorp/743999999999999-senior-software-engineer"
    ref = parse_smartrecruiters_ref(url)
    assert ref is not None
    assert ref.company == "AcmeCorp"
    assert "743999999999999-senior-software-engineer" in ref.posting_id
    assert ref.apply_url == f"https://jobs.smartrecruiters.com/AcmeCorp/{ref.posting_id}/apply"
    assert "smartrecruiters:acmecorp" in ref.vault_scopes

    # Careers host
    url_careers = "https://careers.smartrecruiters.com/AcmeCorp/743999999999999"
    ref2 = parse_smartrecruiters_ref(url_careers)
    assert ref2 is not None
    assert ref2.company == "AcmeCorp"

    # From job object
    import types
    job = types.SimpleNamespace(url=url, source="smartrecruiters")
    assert smartrecruiters_ref_for_job(job) == ref


def test_workday_ref_parsing():
    url = "https://acme.wd3.myworkdayjobs.com/en-US/Acme_Careers/job/Ottawa-ON/Senior-Developer_R12345"
    ref = parse_workday_ref(url)
    assert ref is not None
    assert ref.tenant == "acme"
    assert ref.company == "Acme_Careers"
    assert ref.posting_id == "R12345"
    assert "workday:acme" in ref.vault_scopes

    job = types.SimpleNamespace(url=url, source="workday")
    assert workday_ref_for_job(job) == ref


def test_smartrecruiters_html_parsing():
    html = """
    <html>
      <body>
        <form id="sr-apply-form">
          <label for="firstName">First name</label>
          <input type="text" id="firstName" name="firstName" required />

          <label for="lastName">Last name</label>
          <input type="text" id="lastName" name="lastName" required />

          <label for="email">Email</label>
          <input type="email" id="email" name="email" required />

          <label for="phone">Phone</label>
          <input type="tel" id="phone" name="phoneNumber" />

          <label for="resume">Resume/CV</label>
          <input type="file" id="resume" name="file" required />

          <label for="msg">Message to hiring manager</label>
          <textarea id="msg" name="additionalInformation"></textarea>

          <label>
            <input type="checkbox" name="privacyPolicy" required />
            I agree to the privacy policy and consent to processing.
          </label>
        </form>
      </body>
    </html>
    """
    ref = SmartRecruitersJobRef("Acme", "12345")
    form = parse_smartrecruiters_form(html, ref)
    assert form.platform == "smartrecruiters"
    keys = {c.key for c in form.controls}
    assert "first_name" in keys
    assert "last_name" in keys
    assert "email" in keys
    assert "phone" in keys
    assert "resume" in keys
    assert "cover_letter" in keys
    assert "terms_consent" in keys


def test_workday_html_parsing():
    html = """
    <html>
      <body>
        <div data-automation-id="workday-app">
          <input type="text" data-automation-id="legalNameSection_firstName" name="legalNameSection_firstName" required />
          <input type="text" data-automation-id="legalNameSection_lastName" name="legalNameSection_lastName" required />
          <input type="email" data-automation-id="email" name="email" required />
          <input type="tel" data-automation-id="phone-number" name="phoneNumber" />
          <input type="file" data-automation-id="file-upload-input-ref" name="resumeUpload" required />
          <select data-automation-id="workAuth_authorized" name="authorized">
            <option value="Yes">Yes</option>
            <option value="No">No</option>
          </select>
          <input type="checkbox" data-automation-id="agreementCheckbox" name="terms" required />
        </div>
      </body>
    </html>
    """
    ref = WorkdayJobRef("acme", "acme", "job1")
    form = parse_workday_form(html, ref)
    assert form.platform == "workday"
    keys = {c.key for c in form.controls}
    assert "first_name" in keys
    assert "last_name" in keys
    assert "email" in keys
    assert "resume" in keys
    assert "authorized_to_work" in keys
    assert "terms_agreement" in keys


def test_adapter_registry_and_catalog():
    sr_cls = REGISTRY.get("smartrecruiters")
    assert sr_cls is SmartRecruitersAdapter
    wd_cls = REGISTRY.get("workday")
    assert wd_cls is WorkdayAdapter

    assert ADAPTER_CATALOG["smartrecruiters"].maturity == AdapterMaturity.human_reviewed_submit
    assert ADAPTER_CATALOG["workday"].maturity == AdapterMaturity.human_reviewed_submit


@pytest.mark.anyio
async def test_browser_filling_smartrecruiters_form(tmp_path):
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <form id="sr-form">
          <input type="text" id="firstName" name="firstName" value="" />
          <input type="text" id="lastName" name="lastName" value="" />
          <input type="email" id="email" name="email" value="" />
          <input type="file" id="resume" name="file" />
          <label for="consent"><input type="checkbox" id="consent" name="privacyPolicy" /> I agree to the privacy policy</label>
          <button type="submit" id="submit-btn">Submit Application</button>
        </form>
      </body>
    </html>
    """
    html_file = tmp_path / "sr_form.html"
    html_file.write_text(html, encoding="utf-8")
    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 test resume content")

    answers = {
        "first_name": "Alex",
        "last_name": "Smith",
        "email": "alex.smith@example.com",
        "terms_consent": True,
    }
    files = {"resume": resume_file}

    result = await fill_application_form(
        url=f"file://{html_file}",
        answers=answers,
        files=files,
        labels={"terms_consent": "privacy policy"},
    )
    assert result.success is True
    assert len(result.field_reports) >= 3
    assert len(result.files_attached) == 1
    assert result.files_attached[0].key == "resume"


@pytest.mark.anyio
async def test_browser_filling_workday_form(tmp_path):
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <form id="wd-form">
          <input type="text" data-automation-id="legalNameSection_firstName" name="legalNameSection_firstName" value="" />
          <input type="text" data-automation-id="legalNameSection_lastName" name="legalNameSection_lastName" value="" />
          <input type="email" data-automation-id="email" name="email" value="" />
          <select data-automation-id="workAuth_authorized" name="authorized">
            <option value="Yes">Yes</option>
            <option value="No">No</option>
          </select>
          <input type="file" data-automation-id="file-upload-input-ref" name="resumeFile" />
          <input type="checkbox" data-automation-id="agreementCheckbox" name="terms" />
          <button type="submit" data-automation-id="bottom-navigation-next-button">Submit</button>
        </form>
      </body>
    </html>
    """
    html_file = tmp_path / "wd_form.html"
    html_file.write_text(html, encoding="utf-8")
    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 test workday resume content")

    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "workAuth_authorized": "Yes",
        "agreementCheckbox": True,
    }
    files = {"resume": resume_file}

    result = await fill_application_form(
        url=f"file://{html_file}",
        answers=answers,
        files=files,
    )
    assert result.success is True
    assert len(result.field_reports) >= 4
    assert len(result.files_attached) == 1
    assert result.files_attached[0].key == "resume"
