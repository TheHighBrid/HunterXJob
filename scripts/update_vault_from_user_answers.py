"""Update HunterXJob Answers Vault and profile supplement with user-provided answers."""
import json
import sqlite3
from pathlib import Path
import yaml

VAULT_DIR = Path("docs/Answers Vault")
SEED_JSON = VAULT_DIR / "HunterXJob_Vault_Seed_POST_api_answers.json"
MASTER_YAML = VAULT_DIR / "HunterXJob_Master_Answer_Policy_v3.yaml"
PROFILE_YAML = VAULT_DIR / "HunterXJob_Profile_Supplement_Import.yaml"
DB_PATH = Path("data/hunterxjob-v2.db")

# 1. Update Seed JSON
new_answers = [
    {
        "key": "salary_expectation",
        "value": "60000",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "CAD annual baseline; company/job-specific salary evaluated against range."
    },
    {
        "key": "willing_to_relocate",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not willing to relocate."
    },
    {
        "key": "notice_period",
        "value": "Immediately",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No notice period; can start immediately."
    },
    {
        "key": "availability_date",
        "value": "Immediately",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Can start immediately."
    },
    {
        "key": "current_company",
        "value": "MELATO",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Self-employed founder/operator; not another employer."
    },
    {
        "key": "current_title",
        "value": "Founder & Operator",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "User-owned apparel brand."
    },
    {
        "key": "work_authorization_CA",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Legally entitled to work in Canada. Canadian citizen."
    },
    {
        "key": "canadian_citizen",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Canadian citizen."
    },
    {
        "key": "sponsorship_CA",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Canadian citizen, no sponsorship required."
    },
    {
        "key": "sponsorship_required_CA",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No sponsorship required in Canada."
    },
    {
        "key": "work_authorization_US",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Authorized to work in US; no sponsorship needed."
    },
    {
        "key": "us_citizen",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Not a US citizen (Canadian citizen)."
    },
    {
        "key": "sponsorship_required_US",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No US sponsorship needed."
    },
    {
        "key": "highest_education",
        "value": "College Diploma",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Graduated with College Diploma in Computer Systems (Technique en Systèmes Informatiques) in 2016."
    },
    {
        "key": "education_field",
        "value": "Computer Systems (Technique en Systèmes Informatiques)",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Computer Systems / Technique en Systèmes Informatiques."
    },
    {
        "key": "education_graduation_year",
        "value": "2016",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Graduated in 2016."
    },
    {
        "key": "currently_student",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not currently a student and not planning to return to school soon."
    },
    {
        "key": "plans_return_to_school",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No plans to go back to school in future."
    },
    {
        "key": "professional_certifications",
        "value": "Google AI Fundamentals",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Holds Google AI Fundamentals certificate."
    },
    {
        "key": "quiet_workspace",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Dedicated private, quiet home office suitable for confidential remote work."
    },
    {
        "key": "high_speed_internet",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Has reliable high-speed broadband internet."
    },
    {
        "key": "security_equipment",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Secure home workspace equipment in place."
    },
    {
        "key": "remote_workspace",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Fully equipped for remote work."
    },
    {
        "key": "willing_overtime",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Open to overtime; weekdays preferred."
    },
    {
        "key": "weekend_shifts",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to work weekends if needed (weekdays preferred)."
    },
    {
        "key": "evening_night_shift",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to work evenings, overnight, call center, and fast-paced environments."
    },
    {
        "key": "holiday_work",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to work on holidays."
    },
    {
        "key": "work_travel",
        "value": "Yes (up to 10%)",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to travel up to 10% for work."
    },
    {
        "key": "criminal_convictions",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No criminal record."
    },
    {
        "key": "criminal_record",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No criminal record."
    },
    {
        "key": "police_clearance",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Can provide police certificate / clearance documentation upon request."
    },
    {
        "key": "government_clearance",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Does not currently hold a government security clearance."
    },
    {
        "key": "drivers_license",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Holds valid Ontario G driver's licence."
    },
    {
        "key": "car_ownership",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Owns and has access to a vehicle."
    },
    {
        "key": "direct_underwriting",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Has credit underwriting experience / lending authority."
    },
    {
        "key": "smb_lending",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Has SMB lending experience."
    },
    {
        "key": "sql_skill",
        "value": "Basic",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Basic SQL proficiency."
    },
    {
        "key": "python_skill",
        "value": "Basic",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Basic Python proficiency."
    },
    {
        "key": "computer_skill_level",
        "value": "Advanced",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Advanced computer proficiency."
    },
    {
        "key": "customer_service_years",
        "value": "7+",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Over 7 years of customer-facing experience in financial services and retail."
    },
    {
        "key": "customer_deescalation",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Experienced and skilled in de-escalating customer conflicts."
    },
    {
        "key": "teamwork",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Thrives in team environments."
    },
    {
        "key": "work_independently",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to work independently and self-manage."
    },
    {
        "key": "references_available",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Professional supervisory references available upon request."
    },
    {
        "key": "school_or_work_references",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Consents to employer contacting professional references."
    },
    {
        "key": "reference_check_consent",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Consents to reference checks."
    },
    {
        "key": "noncompete",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not subject to any non-compete agreements."
    },
    {
        "key": "fired_from_job",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Has never been terminated or dismissed from employment."
    },
    {
        "key": "referral_source",
        "value": "Indeed, LinkedIn, Social Media",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Job boards (Indeed, LinkedIn) or company social channels."
    },
    {
        "key": "hear_about_us",
        "value": "Indeed, LinkedIn, Social Media",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Job boards (Indeed, LinkedIn) or company social channels."
    },
    {
        "key": "current_employee_referral",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No employee referral."
    },
    {
        "key": "previously_worked_at_target",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Has not previously worked for company."
    },
    {
        "key": "currently_work_at_target",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not a current employee of target company."
    },
    {
        "key": "friends_or_family_at_target",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No family or household members have worked at target company."
    },
    {
        "key": "relative_on_board",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No relatives on board of directors."
    },
    {
        "key": "shares_in_target",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Does not own material shares in target company."
    },
    {
        "key": "conflict_of_interest",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No conflicts of interest."
    },
    {
        "key": "politically_involved",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not a politically exposed person (PEP)."
    },
    {
        "key": "government_employee_current",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Not currently a public servant."
    },
    {
        "key": "government_employee_past",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Has not been a public servant."
    },
    {
        "key": "future_vacation",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "No upcoming planned vacations."
    },
    {
        "key": "vacation_90_day",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Able to avoid time off in the first 90 days."
    },
    {
        "key": "background_check_consent",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Consents to lawful employment-related background checks."
    },
    {
        "key": "terms_consent",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Accepts terms, conditions, and employment agreements."
    },
    {
        "key": "agreement_consent",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Accepts standard agreements."
    },
    {
        "key": "salary_acceptance",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Accepts offered salary within expected range."
    },
    {
        "key": "working_conditions_acceptance",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Accepts standard working conditions."
    },
    {
        "key": "language_proficiency",
        "value": "French (Native)|English (Fluent)|Arabic (Fluent)|Spanish (Conversational)",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Fully bilingual French/English; also speaks Arabic and conversational Spanish."
    },
    {
        "key": "native_language",
        "value": "French",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "French native."
    },
    {
        "key": "english_proficiency",
        "value": "Fluent",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Fluent English."
    },
    {
        "key": "french_proficiency",
        "value": "Native",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Native French."
    },
    {
        "key": "disability_status",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No disability."
    },
    {
        "key": "disability",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No disability."
    },
    {
        "key": "accommodation_required",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "No accessibility accommodations required."
    },
    {
        "key": "gender",
        "value": "Man",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Man (Cisgender)."
    },
    {
        "key": "gender_identity",
        "value": "Man",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Cisgender Man."
    },
    {
        "key": "sexual_orientation",
        "value": "Heterosexual",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Heterosexual / Straight."
    },
    {
        "key": "visible_minority",
        "value": "Yes",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Member of visible minority."
    },
    {
        "key": "race_ethnicity",
        "value": "Black",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Black."
    },
    {
        "key": "veteran_status",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Not a veteran / military forces member."
    },
    {
        "key": "indigenous_identity",
        "value": "No",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Not Indigenous / Native."
    },
    {
        "key": "allergies",
        "value": "Penicillin",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": True,
        "note": "Allergic to Penicillin; no other allergies."
    },
    {
        "key": "city",
        "value": "Ottawa",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Ottawa, ON."
    },
    {
        "key": "state",
        "value": "Ontario",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Ontario, Canada."
    },
    {
        "key": "country",
        "value": "Canada",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Canada."
    },
    {
        "key": "linkedin_url",
        "value": "https://www.linkedin.com/in/mohamedalem",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "LinkedIn profile."
    },
    {
        "key": "website_url",
        "value": "https://melato.ca",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Business website."
    },
    {
        "key": "why_company_motivation",
        "value": "I thrive in a collaborative, transparent, and high-trust work culture that values operational excellence, continuous learning, and mutual respect. What motivates me is solving meaningful problems, delivering measurable value to clients and team members, and applying my background in financial operations, risk analysis, and customer success to achieve shared goals.",
        "source": "user",
        "scope": "global",
        "confidence": 1.0,
        "sensitive": False,
        "note": "Candidate motivation and work culture answer."
    }
]

# Write updated seed JSON
with open(SEED_JSON, "w", encoding="utf-8") as f:
    json.dump(new_answers, f, indent=2, ensure_ascii=False)
print(f"Updated {SEED_JSON} with {len(new_answers)} verified answer policies.")

# 2. Update Profile YAML
with open(PROFILE_YAML, "r", encoding="utf-8") as f:
    profile_data = yaml.safe_load(f)

profile_data["verified"] = True
profile_data["work_authorization"] = [
    {
        "country": "CA",
        "authorized": True,
        "requires_sponsorship": False,
        "status": "Citizen"
    },
    {
        "country": "US",
        "authorized": True,
        "requires_sponsorship": False,
        "status": "Authorized (No sponsorship needed)"
    }
]

# Ensure languages include Arabic and Spanish
profile_data["languages"] = [
    {"name": "French", "proficiency": "Native"},
    {"name": "English", "proficiency": "Fluent"},
    {"name": "Arabic", "proficiency": "Fluent"},
    {"name": "Spanish", "proficiency": "Conversational"}
]

# Ensure skills include credit underwriting, SMB lending, basic SQL/Python
skill_names = {s.get("name") for s in profile_data.get("skills", [])}
new_skills = [
    {"name": "Credit Underwriting", "level": "Good"},
    {"name": "SMB Lending", "level": "Good"},
    {"name": "SQL", "level": "Basic"},
    {"name": "Python", "level": "Basic"},
    {"name": "Customer Service", "level": "Advanced (7+ years)"},
    {"name": "Conflict De-escalation", "level": "Advanced"}
]
for ns in new_skills:
    if ns["name"] not in skill_names:
        profile_data["skills"].append(ns)

profile_data["education"] = [
    {
        "degree": "College Diploma",
        "field": "Computer Systems (Technique en Systèmes Informatiques)",
        "graduation_year": "2016",
        "completed": True
    }
]

profile_data["certifications"] = [
    {
        "name": "Google AI Fundamentals",
        "issuer": "Google",
        "verified": True
    }
]

with open(PROFILE_YAML, "w", encoding="utf-8") as f:
    yaml.dump(profile_data, f, sort_keys=False, allow_unicode=True)
print(f"Updated {PROFILE_YAML} with verified status and complete facts.")

# 3. Update Master Answer Policy YAML
with open(MASTER_YAML, "r", encoding="utf-8") as f:
    master_data = yaml.safe_load(f)

# Map of question_bank key -> updates
key_updates = {
    "criminal_convictions": {"mode": "auto", "answer": "No"},
    "police_clearance": {"mode": "auto", "answer": "Yes"},
    "government_clearance": {"mode": "auto", "answer": "No"},
    "drivers_license": {"mode": "auto", "answer": "Yes"},
    "car_ownership": {"mode": "auto", "answer": "Yes"},
    "us_citizen": {"mode": "auto", "answer": "No"},
    "sponsorship_required_US": {"mode": "auto", "answer": "No"},
    "quiet_workspace": {"mode": "auto", "answer": "Yes"},
    "high_speed_internet": {"mode": "auto", "answer": "Yes"},
    "security_equipment": {"mode": "auto", "answer": "Yes"},
    "willing_overtime": {"mode": "auto", "answer": "Yes"},
    "weekend_shifts": {"mode": "auto", "answer": "Yes"},
    "evening_night_shift": {"mode": "auto", "answer": "Yes"},
    "work_travel": {"mode": "auto", "answer": "Yes (up to 10%)"},
    "direct_underwriting": {"mode": "auto", "answer": "Yes"},
    "smb_lending": {"mode": "auto", "answer": "Yes"},
    "sql_skill": {"mode": "auto", "answer": "Basic"},
    "python_skill": {"mode": "auto", "answer": "Basic"},
    "highest_education": {"mode": "auto", "answer": "College Diploma"},
    "education_graduation_year": {"mode": "auto", "answer": "2016"},
    "currently_student": {"mode": "auto", "answer": "No"},
    "professional_certifications": {"mode": "auto", "answer": "Google AI Fundamentals"},
    "references_available": {"mode": "auto", "answer": "Yes"},
    "school_or_work_references": {"mode": "auto", "answer": "Yes"},
    "noncompete": {"mode": "auto", "answer": "No"},
    "current_employee_referral": {"mode": "auto", "answer": "No"},
    "friends_or_family_at_target": {"mode": "auto", "answer": "No"},
    "relative_on_board": {"mode": "auto", "answer": "No"},
    "shares_in_target": {"mode": "auto", "answer": "No"},
    "terms_consent": {"mode": "auto", "answer": "Yes"},
    "reference_check_consent": {"mode": "auto", "answer": "Yes"},
    "vacation_90_day": {"mode": "auto", "answer": "Yes"},
    "future_vacation": {"mode": "auto", "answer": "No"},
    "accommodation_required": {"mode": "auto", "answer": "No"},
    "disability_status": {"mode": "auto", "answer": "No"},
    "gender": {"mode": "auto", "answer": "Man"},
    "sexual_orientation": {"mode": "auto", "answer": "Heterosexual"},
    "visible_minority": {"mode": "auto", "answer": "Yes"},
    "race_ethnicity": {"mode": "auto", "answer": "Black"},
    "veteran_status": {"mode": "auto", "answer": "No"},
    "indigenous_identity": {"mode": "auto", "answer": "No"},
    "allergies": {"mode": "auto", "answer": "Penicillin"},
    "government_employee_current": {"mode": "auto", "answer": "No"},
    "government_employee_past": {"mode": "auto", "answer": "No"},
    "conflict_of_interest": {"mode": "auto", "answer": "No"},
    "hear_about_us": {"mode": "auto", "answer": "Indeed, LinkedIn, Social Media"},
    "unmapped_user_answer_8": {"mode": "auto", "answer": "No", "notes": "Mapped to question 8 (No non-compete agreements / no restrictions)."}
}

qb = master_data.get("question_bank", [])
updated_qb_count = 0
for item in qb:
    k = item.get("key")
    if k in key_updates:
        for attr, val in key_updates[k].items():
            item[attr] = val
        updated_qb_count += 1

with open(MASTER_YAML, "w", encoding="utf-8") as f:
    yaml.dump(master_data, f, sort_keys=False, allow_unicode=True)
print(f"Updated {MASTER_YAML} question_bank ({updated_qb_count} policies upgraded to verified answers).")

# 4. Seed the database
import sys
sys.path.insert(0, str(Path("v2").resolve()))
from app.db import SessionLocal, init_db
from app.vault_store import seed_answer_vault_from_policy

init_db()
with SessionLocal() as session:
    res = seed_answer_vault_from_policy(session, force=True, include_master_policy=True)
    print(f"Seeded answer vault in sqlite DB: {res}")
