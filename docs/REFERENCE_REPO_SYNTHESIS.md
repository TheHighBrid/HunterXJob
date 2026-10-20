# HunterXJob Reference Repository Synthesis

This document records the clean-room analysis used to strengthen HunterXJob. It compares the reference projects, identifies the useful engineering patterns in each, and shows how one project's gap can be filled by another project's stronger design.

No source code from the reference repositories was copied into this implementation. The new decision core, answer vault, liveness classifier, tests, and integration were written from scratch against HunterXJob's existing contracts.

## Product direction

HunterXJob should remain a local-first job-search operating system, not a high-volume click bot.

The target product combines:

1. API-first job discovery.
2. Durable normalization, deduplication, and liveness checks.
3. Deterministic vetoes before any AI call.
4. Explainable, multi-dimensional job scoring.
5. Truthful material generation from verified resume facts.
6. Provenance-locked answers for consequential application questions.
7. Isolated platform adapters with validate-before-submit behavior.
8. Review, dry-run, and explicitly unlocked autonomous modes.
9. Evidence capture and an append-only event trail.
10. A mobile-friendly operating dashboard.

## Reference matrix

| Reference | Strongest contribution | Main gap or risk | HunterXJob use |
|---|---|---|---|
| `AkbarDevop/ai-job-agent` | Coach-style orchestration across evaluation, applying, tracking, outreach, follow-up, inbox triage, and dashboards | Claude-specific skill packaging and browser scripts are less portable than a service contract | Adopt the lifecycle and verb-oriented orchestration model; keep execution behind HunterXJob services and adapters |
| `RajjjAryan/career-copilot` | Structured evaluation, company-board scanning, liveness checks, ATS document generation, interview preparation, and integrity checks | Strong decision support, but less emphasis on a durable application execution runtime | Use explicit scoring evidence, liveness, and integrity patterns around HunterXJob's durable pipeline |
| `Vlad9572324/hh.ru-clicker` | Operational dashboard with account state, rate-limit ETA, real-time events, history, and response metrics | Narrow platform scope and no clearly detected repository license | Reimplement observability concepts only; do not copy code |
| `Rayyan9477/AutoApply-AI-Agentic-Browser-Automation-for-Job-Search` | Broad full-stack design, provider abstraction, workers, browser automation, analytics, and semantic matching | Infrastructure is heavy for Android/local-first use; no clearly detected repository license | Retain adapter and worker boundaries while avoiding Redis, cloud, and vector-database requirements in the default path |
| `jolie-z/Auto-JobHunter` | Deterministic vetoes plus a drafter, critic, and formatter material pipeline | Non-commercial/all-rights-reserved terms make direct reuse unsuitable | Clean-room implementation of veto-first decisions and a future reviewer/reviser stage |
| `surapuramakhil-org/Job_search_agent` | Continuous scanning, filters, blacklist handling, personalized responses, bulk workflow, and tracking | AGPL obligations and bulk-first behavior | Reimplement source scheduling and policy filters without importing code or defaulting to mass submission |
| `imon333/Job-apply-AI-agent` | Batch job analysis, CV tailoring, output packaging, and a simple web workflow | License signals are not sufficiently clear for code reuse; execution depth is limited | Use batch UX ideas and artifact packaging only |
| `DaKheera47/job-ops` | Unified search, job scoring, tailored exports, email status detection, sponsorship checks, and a deliberate manual-apply posture | Commons Clause plus AGPL terms restrict commercial reuse; execution is intentionally manual | Reimplement the search/status/sponsorship concepts while preserving HunterXJob's own adapter contracts |
| `feder-cr/Jobs_Applier_AI_Agent_AIHawk` | Mature provider abstraction and broad ATS automation architecture | AGPL and higher platform-policy risk when used for unsupervised volume | Treat as an architectural reference for provider boundaries, not a code donor |
| `santifer/career-ops` | Strong data contract, weighted evaluation, legitimacy checks, research/outreach, tailored PDFs, and single-source-of-truth discipline | Focused more on decision operations than durable device-local browser execution | Combine its evidence discipline with HunterXJob's SQLite state machine and Android CDP runtime |
| Open Grind | Reproducible Android release and security hygiene | It is an unofficial Grindr client, not a job-search project | Exclude all product logic; only retain generic release-hygiene lessons |

## Gap-filling map

### 1. Brittle browser loops

**Seen in:** projects that mix discovery, scoring, form filling, and submission in one script.

**Filled by:** HunterXJob's API-first Greenhouse/Lever discovery plus its `PlatformAdapter` interface.

**Rule:** discovery must not require a browser when a public endpoint exists. Browser code receives normalized jobs and application materials, never raw search responsibility.

### 2. Opaque AI decisions

**Seen in:** systems that expose one unexplained match percentage.

**Filled by:** the new deterministic decision core, influenced by structured evaluation patterns in Career Copilot and Career Ops.

**Rule:** hard vetoes run first. Eligible jobs receive six visible dimensions: role relevance, location fit, sector fit, language fit, seniority fit, and evidence quality.

### 3. Fabricated or weakly sourced answers

**Seen in:** form fillers that let an LLM answer authorization, sponsorship, salary, clearance, or demographic fields.

**Filled by:** the new `AnswerVault`.

**Rule:** consequential fields require explicit user or policy provenance. Generated values cannot satisfy required fields. Low-confidence values stop for review.

### 4. Dead or misleading job links

**Seen in:** batch systems that score cached listings without checking whether the application is still open.

**Filled by:** the new liveness classifier.

**Rule:** expired, blocked, live, and ambiguous pages are distinct states. An access challenge is never reported as an expired job or a successful application.

### 5. High-volume submission risk

**Seen in:** clickers and bulk auto-apply projects.

**Filled by:** HunterXJob's review, dry-run, and autonomous modes, hard live-submission gate, daily limits, and evidence requirements.

**Rule:** no CAPTCHA bypass, no stealth or anti-detection tooling, no invented answers, and no success status without confirmation evidence.

### 6. Weak continuity and auditability

**Seen in:** scripts that log only a final CSV row.

**Filled by:** HunterXJob's SQLite state machine and `PipelineEvent` ledger.

**Rule:** every transition records the previous state, next state, reason, and structured evidence. Retrying does not erase prior work.

### 7. Poor operating visibility

**Seen in:** terminal-only agents with no clear view of blocked work, pending reviews, or funnel health.

**Filled by:** HunterXJob's Expo mobile dashboard, plus observability concepts from hh.ru-clicker and the terminal dashboard in ai-job-agent.

**Rule:** surface queue health, stage counts, review blockers, recent events, confirmation status, and application outcomes rather than vanity totals alone.

### 8. Resource-heavy defaults

**Seen in:** architectures that require Redis, Docker, hosted vector databases, or paid APIs.

**Filled by:** HunterXJob v2's Android-first SQLite, FastAPI, local Ollama, and shared CDP model.

**Rule:** advanced infrastructure may be optional, but the default runtime stays local, zero-cost, resumable, and usable on non-root Android.

## Unified architecture

```text
Candidate profile + verified answer vault + search policy
                         |
                         v
Discovery adapters: Greenhouse | Lever | Ashby | feeds | manual import
                         |
                         v
Normalize -> deduplicate -> liveness classification -> durable Job row
                         |
                         v
Hard vetoes -> six-dimension deterministic score -> optional AI evaluation
                         |
              reject / review / shortlist
                         |
                         v
Materials: facts -> drafter -> reviewer -> reviser -> renderer
                         |
                         v
Answer resolution with provenance and confidence gates
                         |
                         v
Platform adapter: open -> identify -> fill -> upload -> validate
                         |
                  review / dry-run / submit
                         |
                         v
Evidence: screenshot + HTML + trace + confirmation + event ledger
                         |
                         v
Inbox/status inference + follow-up queue + mobile operating dashboard
```

## Implemented in this synthesis branch

- `v2/app/decisioning.py`
  - deterministic vetoes
  - six weighted dimensions
  - explicit reject, review, and shortlist decisions
  - evidence and review flags
- `v2/app/answer_vault.py`
  - value provenance
  - sensitive-field policy
  - confidence thresholds
  - fail-closed resolution
- `v2/app/liveness.py`
  - live, expired, blocked, and ambiguous classification
- `v2/app/pipeline.py`
  - delegates deterministic eligibility to the decision core
  - writes the score breakdown into pipeline-event payloads
- `v2/app/discovery.py`
  - fixes whitespace normalization around HTML block boundaries
- `v2/tests/test_decision_core.py`
  - tests vetoes, scoring dimensions, sensitive-answer provenance, and liveness

## Next implementation slices

1. Persist answer-vault records and their source history in SQLite.
2. Run liveness checks before scoring and again before browser execution.
3. Add a material reviewer/reviser pass with factual-diff checks.
4. Add Ashby and Jobvite discovery adapters before browser-only fallbacks.
5. Expose dimension scores, review flags, and liveness in the mobile dashboard.
6. Add inbox-derived status events with confidence and source-message references.
7. Add adapter certification fixtures that prove dry-run behavior, evidence capture, and confirmation handling per platform.

## Explicit exclusions

- CAPTCHA solving or bypass.
- Browser fingerprint spoofing or stealth packages.
- Platform rate-limit evasion.
- Invented candidate facts.
- Automatic answers to sensitive questions without explicit provenance.
- Treating a click, navigation, or HTTP success as submission confirmation.
- Copying code from non-permissive, unclear, AGPL, or Commons Clause sources into HunterXJob.
