# HunterXJob | Master Applicant Answer & Truth Policy v3.0

**Prepared 2026-10-27** for Mohamed Alem. Personal profile contents are private; avoid committing the file to a public GitHub repository. Designed to *supplement* the existing HunterXJob profile and answer vault.

## What's in this pack

1. `HunterXJob_Master_Answer_Policy_v3.yaml`: comprehensive portable rulebook containing questions, synonyms, fallbacks, decisions and safety gates. This is **not** a native HunterXJob import format. Build a matcher/compiler if integration is needed.
2. `HunterXJob_Profile_Supplement_Import.yaml`: structurally compatible with `./hunterx profile import ...`, but deliberately `verified: false`. Import alongside existing facts; review differences and verify as appropriate. Do not replace an existing more accurate profile or create duplicate employment records.
3. `HunterXJob_Vault_Seed_POST_api_answers.json`: list of payloads compatible with `POST /api/answers` in current HunterXJob v2. API currently accepts individual records; it does **not** have a native bulk-import route.
4. `HunterXJob_Full_Question_Book.md`: human-readable rules and matching question inventory.
5. `seed_vault.py`: optional minimal local batch helper. Dry-run by default; requires explicitly passing `--apply` to write answers to the API. Does not submit any job application.

## Verified integration points from actual repositories

- JobTomatik source: `backend/app/services/answer_policy_catalog.py`, readiness contract at `docs/architecture/ANSWER_POLICY_READINESS.md`.
- HunterXJob v2 source: `v2/app/answer_vault.py`, `v2/app/vault_store.py`, `v2/app/profile.py`, `v2/app/profile_vault.py`, `v2/app/main.py` and `v2/examples/profile.example.yaml`.
- HunterXJob: `./hunterx profile import FILE` imports YAML profile; `POST /api/answers` stores one `AnswerIn` policy. Fallback semantics require an integration layer; simply uploading a YAML does not make a question matcher work.
- Current HunterXJob `AnswerVault` checks user/policy source for sensitive questions and requires confidence 1.0, yet the current database `AnswerPolicy` schema does not natively persist the entire JobTomatik readiness contract. Implement more complex confirmation/expiry/scoping features separately before treating them as enforced.

## How to use (without overwriting anything)

**Read the complete rules first.** If a local HunterXJob installation already contains correct profile facts, do not re-import the supplement unnecessarily.

```bash
# Optional native profile import (review existing profile first)
cd HunterXJob/v2
./hunterx profile list
./hunterx profile import /path/to/HunterXJob_Profile_Supplement_Import.yaml
./hunterx profile list --unverified

# Optional answer-vault seed: first preview; then explicitly apply if desired
python /path/to/seed_vault.py --file /path/to/HunterXJob_Vault_Seed_POST_api_answers.json
HUNTERXJOB_API_KEY='LOCAL_KEY' python /path/to/seed_vault.py --file /path/to/HunterXJob_Vault_Seed_POST_api_answers.json --apply
```

`seed_vault.py` defaults to `http://127.0.0.1:8011`. Secrets come only from your environment, never from downloaded profile files or CLI argument values. Reconcile existing policies first because the endpoint **upserts** a `(key, scope)` pair and will overwrite it on apply. The helper does not bypass that.

## Decision table

| Event | Action |
| --- | --- |
| Exact approved fact and unambiguous field | Autofill using correct control type and one authorized fallback form |
| Reworded synonym, high-confidence meaning, compatible options | Autofill after polarity, units, scope and employer check |
| Required field but unknown | Stop that application for a targeted manual review; continue other unrelated applications |
| Optional demographic / medical / marketing item | Leave blank, unless an explicit, approved decline choice exists and is appropriate |
| Citizenship, clearances, convictions, sponsorship, credentials | Never infer from other categories; require direct proof/owner approval |
| Question about employer history | Check employer-specific ledger; do not default No to prior application or prior work |
| Applicant consents to background checks | Yes, where it is a standalone lawful screening consent; separate broader agreements for review |
| User says US authorized | Record this assertion; still skip US opportunities because US targeting is OFF |
| Company demands physical presence outside Ottawa/Gatineau | Exclude, not 'I can relocate' or 'Yes' |
| Vacancy says remote, but requires an onsite trip or residency outside user location | Exclude/review based on actual requirement |
| Asked to consent to recording, retain profile, AI evaluation or company T&C | No blanket approval, review the exact request |
| Salary entered as invalid format | Try equivalent formatting; never jump from 60k to 107k just because parser rejected 60000 |
| CAPTCHAs, proctored tests and legal declarations | Human handoff, do not evade or silently accept |

### Conditional answers

**Employment:** Self-employment means Yes to "Do you work/run a business?" and No to "Are you currently employed by another company?". A plain "Are you currently employed?" often calls for `Self-employed` or an appropriate employer-provided category, not No.

**Work locations:** Ottawa and Gatineau permit onsite/hybrid/remote; rest of Canada must be genuinely 100% remote, permitting employment from Ottawa. US and international discovery are disabled. No relocation.

**Salary:** `60000`, `60,000`, `60 000`, `60K`, `60.000` are display-format equivalents. `70000`, `85000`, `95000`, `107000` are strategic salary expectations only if a role range warrants them. Do not swap currencies or units.

**Education:** Two-year computer systems studies are known, but the document award status is unconfirmed. Do not claim a bachelor's or professional certification.

**Identity:** Full name, phone, email and LinkedIn came from prior profile context and should be reconciled against the most recently verified HunterXJob profile if conflict appears. Do not upload SIN, exact home address, date of birth, or other secret identifiers in a bulk policy file.

**Unmapped input:** Original numbered item `8. No.` has no attached question and is intentionally disabled.

## Example of role-specific written answers

**Why this company?**
> The role stands out because it connects [a verified company product, mission or responsibility] with the experience I have developed in Canadian banking, investigations, account operations and bilingual customer-facing work. I would bring structured judgment, strong communication and experience navigating complex cases to [specific work described in the posting].

**Why this role?**
> My background spans fraud and disputes, collections, portfolio management and supervisory responsibilities. The opportunity to apply those strengths to [a real requirement from the posting] makes this a relevant next step, while offering room to grow in [a genuine professional growth area].

**Leadership experience**
> At RBC and Scotiabank I had supervisory responsibilities that included handling escalations, coaching, reviewing performance and preparing reports. Those experiences strengthened my ability to make sound decisions, support teammates and stay organized under pressure.

**Explain current employment**
> I currently operate MELATO, my apparel brand. I am available to begin a new role immediately and can arrange an appropriate transition for my business responsibilities.

Do not present any template placeholder as a real employer fact. Run a truth guard against source profile facts before output.

## Comprehensive inventory

### Compensation (5 families)
- **salary_expectation** | Example: *What are your salary expectations?* | **60000** | Mode: `auto`
  - Variants: `60,000`, `60 000`, `60K`, `60k`, `60.000`, `$60,000 CAD`, `CAD 60,000`
  - Rule: CAD annual baseline. Formatting variants mean 60000. Salary figures 70000/85000/95000/107000 are NEGOTIATION LEVELS, not equivalent-format fallbacks. May choose appropriate higher expectation only with role-level evidence and pay range.
- **minimum_acceptable_salary** | Example: *What is the minimum annual salary you would accept?* | **60000** | Mode: `auto`
  - Variants: `$60,000 CAD`, `60K`
  - Rule: Hard targeting minimum. Do not use this number for hourly or USD field without validated currency/units conversion.
- **salary_history** | Example: *What was your previous salary?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not substitute desired salary for historical earnings. May leave blank when optional and follow relevant legal requirements.
- **compensation_type** | Example: *Hourly or salaried?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Default CAD annual baseline where asked; do not invent compensation preferences for commission, equity or bonus.
- **salary_currency** | Example: *What currency do you expect to be paid in?* | **CAD** | Mode: `auto`
  - Variants: `Canadian dollars`, `Canadian Dollar`
  - Condition: `job.country == CA`

### Availability (7 families)
- **start_availability** | Example: *When can you start?* | **Immediately** | Mode: `auto`
  - Variants: `Available immediately`, `As soon as possible`, `ASAP`, `Immediate start`, `In 2 weeks`, `Within 14 days`
  - Rule: Immediately is preferred. Two weeks only if immediately is not offered, or the actual employer start requirements justify it. Dates must be calculated when a date picker requires YYYY-MM-DD; never enter words in a date input.
- **notice_period** | Example: *What is your notice period?* | **0** | Mode: `auto`
  - Variants: `No notice required`, `Immediately`, `0 weeks`, `Two weeks`, `2 weeks`, `14 days`
  - Rule: 0 is the truthful default for preferred start. An optional two-week transition is flexibility, not an obligatory notice period.
- **vacation_90_day** | Example: *Can you avoid time off in your first 90 days?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not claim or promise absence of vacation without owner confirmation and job specifics.
- **future_vacation** | Example: *Do you have any planned vacations?* | **Not pre-authorized / use context** | Mode: `review`
- **willing_overtime** | Example: *Can you work overtime?* | **Not pre-authorized / use context** | Mode: `review`
- **evening_night_shift** | Example: *Can you work evenings?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Past night-shift work at RBC does not automatically establish current availability for every schedule.
- **weekend_shifts** | Example: *Are you willing to work weekends?* | **Not pre-authorized / use context** | Mode: `review`

### Employment (9 families)
- **self_employed** | Example: *Are you self-employed?* | **Yes** | Mode: `auto`
  - Variants: `Self-employed`, `Yes, I own and operate MELATO, an apparel brand.`
- **currently_employed_any** | Example: *Are you currently employed?* | **Self-employed** | Mode: `auto`
  - Variants: `Yes, self-employed`, `Business owner`, `Founder and operator of MELATO`
  - Rule: Do not answer No solely because the candidate is not employed by a separate company. If options are Employed/Unemployed, select Employed only if employer definitions include self-employment; otherwise review.
- **currently_employed_elsewhere** | Example: *Are you presently employed by another organization?* | **No** | Mode: `auto`
  - Variants: `No, I am self-employed running MELATO.`
  - Rule: Distinguish from being self-employed.
- **current_company** | Example: *What is your current company?* | **MELATO** | Mode: `auto`
  - Variants: `Melato`, `MELATO apparel brand`, `Self-employed, MELATO`
- **current_role** | Example: *What is your current title?* | **Founder & Operator** | Mode: `auto`
  - Variants: `Business Owner`, `Founder`, `Owner / Creative Director`, `Founder and operator of MELATO`
  - Rule: Designer and Creative Director is also a current business role; select based on question context.
- **previously_worked_at_target** | Example: *Have you ever worked for us?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Compare exact legal employer and acquisition/subsidiary before answering. Do not default No. Existing history includes RBC, TD, BMO, Scotiabank/Tangerine; those are excluded from search, not erased from truth.
- **currently_work_at_target** | Example: *Are you currently an employee of [COMPANY]?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: No for companies other than user-owned MELATO, barring evidence of a current separate employment relationship.
- **reason_for_leaving** | Example: *Why did you leave your previous job?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Employer-specific and accuracy-sensitive. Scotiabank/Tangerine contract finished; older roles have specific history. Never imply dismissal for performance or fabricate a universal leaving reason.
- **previous_employers** | Example: *List previous employers* | **Not pre-authorized / use context** | Mode: `generate_verified`
  - Rule: Use verified HunterXJob profile to populate RBC, Scotiabank/Tangerine, BMO, TD and MELATO with exact titles and dates. Do not use this Q&A bank to override current verified résumé chronology.

### Geography (7 families)
- **relocation** | Example: *Are you willing to relocate?* | **No** | Mode: `auto`
  - Variants: `Not open to relocation`, `I am based in Ottawa and am not seeking relocation.`
- **onsite_or_hybrid_local** | Example: *Are you able to work on-site in Ottawa?* | **Yes** | Mode: `auto`
  - Variants: `Yes, on-site, hybrid, or remote are all acceptable in Ottawa/Gatineau.`
  - Condition: `job.location_in_ottawa_gatineau == true`
- **onsite_or_hybrid_nonlocal** | Example: *Can you work in person in Toronto?* | **No** | Mode: `auto`
  - Variants: `No, remote only outside Ottawa/Gatineau.`
  - Rule: Filter job out before answering when on-site/hybrid outside the Ottawa-Gatineau area. A Canadian remote role may still have province-specific employment restrictions.
  - Condition: `job.location_in_ottawa_gatineau == false`
- **work_arrangement_local** | Example: *Which working arrangement do you prefer?* | **No preference** | Mode: `auto`
  - Variants: `Remote / Hybrid / On-site`, `Flexible`, `Any of the three`
  - Condition: `job.location_in_ottawa_gatineau == true`
- **work_arrangement_nonlocal** | Example: *What work setup would you consider if the role is outside Ottawa/Gatineau?* | **Remote** | Mode: `auto`
  - Variants: `Fully remote`, `100% remote`, `Work from home`
  - Condition: `job.location_in_ottawa_gatineau == false`
- **remote_location_eligibility** | Example: *Do you reside in Ontario?* | **Ottawa, Ontario, Canada** | Mode: `auto`
  - Variants: `Ottawa, ON`, `Ontario`, `Canada`
  - Rule: Only choose an option if it correctly corresponds to the precise question. Never choose California from an Ontario-like selector.
- **remote_other_country** | Example: *Can you work remotely from the US?* | **DISABLED: unassigned answer; never apply** | Mode: `disabled`
  - Rule: US/international discovery disabled. US work authorization and country of residence are separate facts. No remote-for-US assurances.

### Legal (20 families)
- **canadian_work_authorization** | Example: *Are you legally authorized to work in Canada?* | **Yes** | Mode: `auto`
  - Variants: `I am legally authorized to work in Canada`, `Authorized`, `Eligible`, `Oui`
  - Rule: User provided Canadian citizenship/work history; use only for Canada.
  - Condition: `job.country == CA`
- **us_work_authorization** | Example: *Are you legally authorized to work in the United States?* | **Yes** | Mode: `auto`
  - Variants: `Legally authorized to work in the United States`
  - Rule: User explicitly states US work authorization; does NOT establish US citizenship, visa type, remote payroll eligibility or sponsorship status. US search currently disabled.
  - Condition: `job.country == US`
- **background_check_consent** | Example: *Do you consent to a background check?* | **Yes** | Mode: `auto`
  - Variants: `I consent`, `I agree`, `Yes, I consent to lawful employment-related background checks.`
  - Rule: User explicitly authorized background-check consent. A specific employer authorization agreement with additional terms may require review; consenting does not imply a clean criminal record.
- **noncompete** | Example: *Are you subject to a non-compete?* | **Not pre-authorized / use context** | Mode: `review`
- **criminal_convictions** | Example: *Have you ever been convicted of a criminal offence?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: UNKNOWN. Background-check consent is not a criminal-history answer. Follow exact jurisdictional question, time limit, pardon and exceptions.
- **police_clearance** | Example: *Can you provide a police certificate?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Willingness to screening is established; eligibility/result and specialized checks are not.
- **canadian_citizen** | Example: *Are you a Canadian citizen?* | **Yes** | Mode: `auto`
  - Variants: `Canadian`, `Canada`, `Canadian citizen`
  - Rule: Based on prior user-provided citizenship. Separate from work authorization or PR status.
- **us_citizen** | Example: *Are you a United States citizen?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: US work authorization does not establish US citizenship. US roles are currently disabled.
- **immigration_status_US** | Example: *What is your US immigration or work visa status?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Authorization is user-confirmed; immigration class is unknown.
- **sponsorship_required_CA** | Example: *Will you now or in the future require Canadian immigration sponsorship?* | **No** | Mode: `auto`
  - Variants: `No sponsorship required`
  - Rule: Canadian citizen. Do not infer anything about US sponsorship.
  - Condition: `job.country == CA`
- **sponsorship_required_US** | Example: *Will you need employer visa sponsorship now or in the future for US work?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Unknown even though US work authorization is user-confirmed.
  - Condition: `job.country == US`
- **social_insurance_number_valid** | Example: *Do you have a valid Social Insurance Number?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Never enter SIN in applications by default. Number itself must not be in this pack.
- **legal_age** | Example: *Are you at least 18?* | **Yes** | Mode: `auto`
  - Variants: `Over 18`, `I meet the age requirement`
  - Rule: Age 31 in October 2026 user-provided; legal age minimum only. Do not assume DOB.
- **work_eligibility_start** | Example: *Can you provide documentation proving eligibility to work?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Legal right to work known, but not possession of specific documents, type or immediately available copies.
- **birth_date** | Example: *Date of birth?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not infer from age. Do not disclose if optional and unnecessary.
- **gov_id** | Example: *Government ID number?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: NEVER store or autofill identification numbers in the public answer pack. Require secure verified employer-specific collection only when necessary.
- **financial_regulatory_action** | Example: *Are you barred from working in financial services?* | **Not pre-authorized / use context** | Mode: `review`
- **lawsuits_or_pending_charges** | Example: *Any pending criminal charges?* | **Not pre-authorized / use context** | Mode: `review`
- **non_disclosure_agreement** | Example: *Are you bound by an NDA?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Distinguish current agreements from generic willingness to respect employer confidentiality.
- **work_permit_expiry** | Example: *Does your work permit expire?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Canadian citizenship means no Canadian work permit necessary, but exact form may be inapplicable rather than dates. US visa unknown.

### Recruiting (4 families)
- **hear_about_us** | Example: *How did you hear about this job?* | **Company website** | Mode: `auto`
  - Variants: `Company careers page`, `Company website`, `Job board`, `Indeed`, `LinkedIn`, `Social Media`, `Friend`, `Advertisement`, `AI assistant`, `Research`, `Google search`, `Other`
  - Rule: Choose the TRUE discovery channel for each specific posting. Preferred source label is not permission to claim a referral or friend when none exists.
- **applied_to_target_before** | Example: *Have you applied here before?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Require employer-scoped application ledger; absent evidence, review rather than No.
- **prior_rejection** | Example: *Have we declined a previous application from you?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Use employer-specific outcome history only; otherwise review.
- **competing_offers** | Example: *Are you interviewing with other employers?* | **Not pre-authorized / use context** | Mode: `review`

### Language (6 families)
- **bilingual_status** | Example: *Are you bilingual in English and French?* | **Yes** | Mode: `auto`
  - Variants: `Fluent in English and French`, `Bilingual, French and English`, `Fully bilingual`, `Oui`
- **native_language** | Example: *What is your first language?* | **French** | Mode: `auto`
  - Variants: `Français`, `French (native)`
- **english_proficiency** | Example: *How would you rate your English proficiency?* | **Fluent** | Mode: `auto`
  - Variants: `Professional fluency`, `Full professional proficiency`, `Advanced`, `Yes`
- **french_proficiency** | Example: *How would you rate your French proficiency?* | **Native** | Mode: `auto`
  - Variants: `Native speaker`, `Fluent`, `Mother tongue`, `French first language`, `Yes`
- **preferred_work_language** | Example: *Which language do you prefer to work in?* | **English** | Mode: `auto`
  - Variants: `No strong preference`, `Either English or French`, `Bilingual / Both`
  - Rule: Only choose English when a single language is required. Both are acceptable otherwise.
- **language_assessment** | Example: *Can you work professionally in English and French?* | **Yes** | Mode: `auto`
  - Variants: `Fully bilingual in English and French`
  - Rule: Do not claim specific language-test scores, certifications or government clearance results without verified test record.

### Contact (8 families)
- **city** | Example: *What city do you live in?* | **Ottawa** | Mode: `auto`
  - Variants: `Ottawa, ON`, `Ottawa, Ontario`, `Ottawa, ON, Canada`
- **province** | Example: *Which province do you reside in?* | **Ontario** | Mode: `auto`
  - Variants: `ON`
- **country** | Example: *Country of residence?* | **Canada** | Mode: `auto`
  - Variants: `CA`
- **postal_code** | Example: *Postal code?* | **K1K 4G8** | Mode: `auto`
  - Variants: `K1K4G8`
  - Rule: User instructed: provide postal code only when mandatory. Do NOT expand to street address; never treat postal code as exact mailing address.
  - Condition: `field.required == true`
- **full_address** | Example: *What is your full street address?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not invent street address, unit number or neighborhood. If optional, leave blank; if required request an exact address.
- **linkedin_url** | Example: *What is your LinkedIn profile?* | **https://www.linkedin.com/in/mohamedalem** | Mode: `auto`
  - Rule: From prior profile material, should be checked against current live profile before use.
- **personal_website** | Example: *Website?* | **https://melato.ca** | Mode: `auto`
  - Variants: `melato.ca`
  - Rule: This is a business website, not automatically a software-development portfolio.
- **portfolio_url** | Example: *Portfolio?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: For fashion/creative work, MELATO site may be relevant. For a tech/design portfolio that explicitly asks for individual work, do not represent melato.ca as a fully evidenced personal case-study portfolio.

### Unmapped (1 families)
- **unmapped_user_answer_8** | Example: *(Unmapped original instruction #8: No, question not provided)* | **DISABLED: unassigned answer; never apply** | Mode: `disabled`
  - Rule: NEVER map this to any question until the user identifies the intended field.

### Relationships (3 families)
- **current_employee_referral** | Example: *Were you referred by an employee?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: If true referral evidence exists, select Yes + full name. Otherwise No, no fabricated referral.
- **friends_or_family_at_target** | Example: *Do you have any friends or relatives who work here?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Unknown; never assume no. Separate friend, relative, and referral questions.
- **relative_on_board** | Example: *Do you know any member of the board of directors?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: No prior confirmed facts.

### Conflicts (2 families)
- **shares_in_target** | Example: *Do you own stock in our company?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Unknown; financial holding questions are company-specific and may be subject to definition or exceptions.
- **conflict_of_interest** | Example: *Do you have any conflicts of interest?* | **Not pre-authorized / use context** | Mode: `contextual`
  - Rule: Candidate owns MELATO. Disclose self-employment when relevant. Conflict depends on employer policy and role; never automatically mark No.

### Consent (6 families)
- **terms_consent** | Example: *Do you agree to our terms and conditions?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Read actual employer-specific text and confirm it is within pre-approved scope. Do not turn general consent into blanket acceptance of contracts or attestations.
- **reference_check_consent** | Example: *May we contact your references?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Distinct from general background check. Contacting MELATO or previous employers may require an explicit contact preference.
- **share_data_future_roles** | Example: *May we keep your resume for future jobs?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Not necessary for current application. No inferred consent.
- **marketing_consent** | Example: *Subscribe to recruiting news?* | **Skip optional; review required** | Mode: `skip_optional`
- **ai_processing_consent** | Example: *Do you consent to AI analysis of your application?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Separate optional and mandatory processing, and assess employer terms.
- **recorded_interview_consent** | Example: *Do you consent to recording an interview?* | **Not pre-authorized / use context** | Mode: `review`

### Government (4 families)
- **government_clearance** | Example: *Do you currently hold a valid government security clearance?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Unknown. Never infer clearance from banking roles or CRA applications.
- **government_employee_current** | Example: *Are you currently a government employee?* | **No** | Mode: `auto`
  - Variants: `No, I am currently self-employed.`
  - Rule: Based on current self-employment and no current government role; do not use for questions asking ever worked for government.
- **government_employee_past** | Example: *Have you ever been a public servant?* | **Not pre-authorized / use context** | Mode: `review`
- **government_clearance_eligibility** | Example: *Are you eligible for a Canadian security clearance?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not conflate citizenship with all clearance prerequisites.

### Logistics (5 families)
- **drivers_license** | Example: *Do you have a valid driver’s licence?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Unknown licence and class. Do not infer from ability to commute.
- **car_ownership** | Example: *Do you own a vehicle?* | **Not pre-authorized / use context** | Mode: `review`
- **commute_local** | Example: *Can you reliably commute to Ottawa?* | **Yes** | Mode: `auto`
  - Variants: `Yes, for roles in Ottawa or Gatineau.`
  - Rule: Does not prove ownership of car or licence.
  - Condition: `job.location_in_ottawa_gatineau == true`
- **commute_outside_local** | Example: *Can you commute to Toronto three days per week?* | **No** | Mode: `auto`
  - Variants: `Not open to a commute outside Ottawa/Gatineau`
  - Condition: `job.location_in_ottawa_gatineau == false`
- **work_travel** | Example: *Can you travel for work?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Separate short business travel, overnight travel and international travel, and passport validity.

### Demographic (11 families)
- **marital_status** | Example: *Are you married?* | **Skip optional; review required** | Mode: `skip_optional`
- **gender** | Example: *What is your gender?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Do not infer from name or photos. If optional skip; if must choose, offer Prefer not to answer where available; otherwise review.
- **pronouns** | Example: *What are your pronouns?* | **Skip optional; review required** | Mode: `skip_optional`
- **sexual_orientation** | Example: *Do you identify as LGBTQ+?* | **Skip optional; review required** | Mode: `skip_optional`
- **race_ethnicity** | Example: *What is your race?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Do not infer from country of origin, language or name.
- **visible_minority** | Example: *Are you a member of a visible minority?* | **Skip optional; review required** | Mode: `skip_optional`
- **indigenous_identity** | Example: *Do you identify as Indigenous?* | **Skip optional; review required** | Mode: `skip_optional`
- **disability_status** | Example: *Do you identify as a person with a disability?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Do not infer from medical information or ask for sensitive medical disclosures.
- **veteran_status** | Example: *Are you a veteran?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Military service unknown. Never invent No.
- **religious_identity** | Example: *What is your religion?* | **Skip optional; review required** | Mode: `skip_optional`
- **national_origin** | Example: *Where were you born?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Not the same as citizenship; no need to disclose as demographic field.

### Accommodation (1 families)
- **accommodation_required** | Example: *Do you require an accommodation for the hiring process?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Candidate decides. Optional fields may be skipped rather than answered No.

### Qualifications (2 families)
- **professional_organization** | Example: *Are you a member of a professional association?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Distinguish member of any organization from regulated bodies, unions and volunteer affiliations.
- **professional_certifications** | Example: *Do you hold professional certifications?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: No verified professional certificates in supplied context. Do not respond Yes for any named certification. If optional, leave blank.

### Education (4 families)
- **highest_education** | Example: *What is your highest level of education?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: User reports two years of college studies in computer systems and high school. Graduation/award status must be proven before selecting College Diploma or degree.
- **education_field** | Example: *What did you study?* | **Computer Systems (Technique en Systèmes Informatiques)** | Mode: `auto`
  - Variants: `Computer Systems`, `Information Technology / Computer Systems`
  - Rule: Program known. Do not claim bachelor, computer engineering, specific degree.
- **currently_student** | Example: *Are you currently a student?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Historical education does not establish current student status.
- **education_graduation_year** | Example: *What year did you graduate?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: No verified completion date. Never calculate from age.

### Experience (11 families)
- **sales_experience** | Example: *Do you have sales experience?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Banking/customer-facing roles do not prove a defined amount of quota-bearing sales. Ask/rely on verified employment evidence.
- **management_experience** | Example: *Have you held a leadership position?* | **Yes** | Mode: `auto`
  - Variants: `Yes. I held supervisory responsibilities at RBC and Scotiabank, including coaching, escalations, assessments and reporting.`
  - Rule: Do not invent number of direct reports or years of people-management tenure.
- **banking_experience_years** | Example: *How many years of banking experience do you have?* | **7** | Mode: `auto`
  - Variants: `7 years`, `Approximately 7 years`, `Seven years`
  - Rule: Prior user-provided approximate 7 years Canadian banking/payments. No inflation to 8+.
- **portfolio_management_experience** | Example: *Have you managed an account portfolio?* | **Yes** | Mode: `auto`
  - Variants: `Yes, including more than three years of managing large-balance portfolios.`
- **portfolio_management_years** | Example: *How many years of large-balance portfolio management?* | **3+** | Mode: `auto`
  - Variants: `More than 3 years`, `At least 3 years`
  - Rule: Only for large-balance account portfolio scope; does not imply commercial loan portfolio ownership.
- **fraud_experience** | Example: *Have you worked in fraud analysis?* | **Yes** | Mode: `auto`
  - Variants: `Yes, fraud, disputes, escalations and transaction-risk decisions in Canadian banking.`
- **collections_experience** | Example: *Have you worked in collections?* | **Yes** | Mode: `auto`
  - Variants: `Yes, collections, account portfolios and loan-related operations.`
- **customer_facing_experience** | Example: *Do you have customer-facing experience?* | **Yes** | Mode: `auto`
  - Variants: `Yes, including bilingual escalations, customer support and banking operations.`
- **smb_lending** | Example: *Have you worked in SMB lending?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: No verified independent SMB underwriting or policy ownership. Do not convert consumer banking/collections experience into SMB credit underwriting.
- **direct_underwriting** | Example: *Have you personally underwritten credit applications?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Review before saying Yes. Distinguish loan servicing and credit decision support from underwriting authority.
- **compliance_experience** | Example: *Do you have regulated financial services experience?* | **Yes** | Mode: `auto`
  - Variants: `Yes, within Canadian banking and payment operations.`
  - Rule: Do not invent CAMS, FINTRAC investigator duties, securities registration or specialized certifications.

### Remote (7 families)
- **quiet_workspace** | Example: *Do you have a private, quiet workspace?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Not confirmed.
- **high_speed_internet** | Example: *Do you have reliable high-speed internet?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Access to internet does not establish required download/upload speeds or employer uptime SLA.
- **personal_computer** | Example: *Do you own a computer?* | **Yes** | Mode: `auto`
  - Variants: `Yes, I have a MacBook Air.`
  - Rule: M1 MacBook Air user-provided. Does not prove company device-policy or minimum RAM/performance.
- **mobile_phone** | Example: *Do you have a smartphone?* | **Yes** | Mode: `auto`
  - Variants: `Yes, I have a smartphone.`
  - Rule: No claim about specific business phone number unless using verified contact.
- **security_equipment** | Example: *Do you have secure home workspace equipment?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Having a laptop or phone does not guarantee enterprise security equipment or software.
- **internet_billing** | Example: *Are you willing to pay for home internet?* | **Not pre-authorized / use context** | Mode: `review`
- **time_zone** | Example: *What time zone are you in?* | **America/Toronto** | Mode: `auto`
  - Variants: `Eastern Time`, `ET`, `EST/EDT as applicable`
  - Rule: America/Toronto handles DST correctly.

### Medical (1 families)
- **allergies** | Example: *Do you have allergies?* | **Skip optional; review required** | Mode: `skip_optional`
  - Rule: Not relevant to most applications and never infer medical information.

### Skills (7 families)
- **excel_skill** | Example: *Do you have Excel experience?* | **Good** | Mode: `auto`
  - Variants: `Proficient`, `Experienced`, `Yes`
  - Rule: User rating: Good. Do not auto-claim expert VBA, Power Query, Excel macros, Power Pivot or financial modeling.
- **netsuite_skill** | Example: *Have you used NetSuite?* | **Very good** | Mode: `auto`
  - Variants: `Highly proficient`, `Yes`
- **salesforce_skill** | Example: *Have you used Salesforce?* | **Intermediate** | Mode: `auto`
  - Variants: `Moderate`, `Medium`, `Yes`
- **google_ad_manager_skill** | Example: *Experience with Google Ad Manager?* | **Very good** | Mode: `auto`
  - Variants: `Highly proficient`, `Yes`
  - Rule: Google Ad Manager is not Google Analytics or Google Ads certifications.
- **sql_skill** | Example: *Do you know SQL?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Previous application questions about SQL do NOT prove direct job experience. Do not invent SQL fluency, SQL projects, data engineering or years.
- **python_skill** | Example: *Do you have professional Python experience?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Technical interest is not professional Python experience.
- **role_specific_tools** | Example: *Which systems have you worked with?* | **Excel, NetSuite, Salesforce and Google Ad Manager.** | Mode: `generate_verified`
  - Rule: Describe relative proficiency accurately. Do not add SQL, Python, SAP, Power BI, Tableau, AWS or other tools without factual basis.

### Free Text (8 families)
- **supervision_example** | Example: *Describe your leadership experience* | **At RBC and Scotiabank, I supported supervisory operations, including escalations, coaching, assessments and reporting, while helping teams deliver accurate decisions in high-pressure financial services environments.** | Mode: `generate_verified`
  - Rule: May tailor but only from verified accomplishments. Never invent team size, percentages or awards.
- **why_company** | Example: *Why do you want to work here?* | **Not pre-authorized / use context** | Mode: `generate_verified`
  - Rule: Use accurate employer mission/product and job description + candidate banking/operations/leadership skills; no generic unsupported claims. Avoid empty enthusiasm. Example template in writing guide.
- **why_role** | Example: *Why are you applying for this role?* | **Not pre-authorized / use context** | Mode: `generate_verified`
  - Rule: Tie two or three VERIFIED role requirements to matching experience; do not claim unexplained skills.
- **motivation** | Example: *What motivates you?* | **I am looking for a role where I can apply my background in financial operations, fraud, account management and team support to deliver measurable value, while continuing to develop professionally.** | Mode: `generate_verified`
- **greatest_strength** | Example: *What is your greatest strength?* | **My strengths are structured problem-solving, bilingual communication, handling escalations, and making careful decisions in demanding financial-services environments.** | Mode: `generate_verified`
- **greatest_achievement** | Example: *What is your proudest accomplishment?* | **Not pre-authorized / use context** | Mode: `generate_verified`
  - Rule: Requires a real verified result. Do not invent monetary savings or performance rates.
- **difficult_customer** | Example: *Describe a challenging customer situation* | **Not pre-authorized / use context** | Mode: `generate_verified`
  - Rule: Answer as a general approach or grounded example, not invented case narrative, date, dollars or outcome.
- **employment_gap** | Example: *Explain your career gap* | **I have been operating my apparel brand, MELATO, while pursuing my next professional opportunity.** | Mode: `generate_verified`
  - Rule: Do not say unemployed in a way that erases self-employment. Do not invent business revenue.

### References (2 families)
- **references_available** | Example: *Can you provide professional references?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Never invent people, contact info or consent to contact.
- **school_or_work_references** | Example: *Can we contact your current manager?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not contact former employers without approved person and availability.

### Eligibility (1 families)
- **physical_job_requirements** | Example: *Can you lift 50 pounds?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Job-specific accommodation/ability should not be assumed.

### Assessments (1 families)
- **pre_employment_test** | Example: *Are you willing to complete a skills assessment?* | **Not pre-authorized / use context** | Mode: `review`
  - Rule: Do not automatically consent to proctoring/recording or third-party data uses. Can prepare, but cannot impersonate.
