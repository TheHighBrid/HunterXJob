"""Comprehensive test suite for the Autonomous Improvement Kernel."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from improvement_kernel import (
    AgentHarness,
    AgentVersion,
    CandidateSynthesizer,
    ContinuousImprovementLoop,
    DefectClusterer,
    EvaluationEngine,
    FailureCategory,
    HunterXJobAdapter,
    ImprovementRuntime,
    PromotionDecision,
    PromotionGate,
    SandboxRunner,
    TraceRedactor,
    default_harness,
    get_current_champion,
    init_kernel_db,
)
from improvement_kernel.clustering.analyzer import analyze_causal_root_cause
from improvement_kernel.clustering.clusterer import cluster_run_failure
from improvement_kernel.evaluations.tier1 import evaluate_tier1_invariants
from improvement_kernel.evaluations.tier2 import evaluate_tier2_metrics
from improvement_kernel.evaluations.tier3 import evaluate_tier3_judge
from improvement_kernel.evaluations.promotion import compute_cohens_d, compute_wilcoxon_signed_rank
from improvement_kernel.storage.models import (
    DefectCluster,
    DefectStatus,
    KernelRun,
    RunStatus,
    TraceSpan,
    VersionStatus,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    yield session, engine
    session.close()


def test_redactor_scrubs_pii_and_secrets():
    redactor = TraceRedactor()
    raw_text = (
        "Candidate Alex Mercer (alex.mercer@gmail.com, 613-555-0199, SIN 123-456-789) "
        "used api_key: sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 "
        "and bearer token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDNacO7DUMWbgP2iW "
        "with credit card 4111 2222 3333 4444"
    )
    scrubbed = redactor.scrub_text(raw_text)

    assert "alex.mercer@gmail.com" not in scrubbed
    assert "<EMAIL_HASH_" in scrubbed
    assert "613-555-0199" not in scrubbed
    assert "***-***-0199" in scrubbed
    assert "123-456-789" not in scrubbed
    assert "<REDACTED_SSN_SIN>" in scrubbed
    assert "sk-proj" not in scrubbed
    assert "<REDACTED_API_KEY>" in scrubbed
    assert "4111 2222 3333 4444" not in scrubbed
    assert "<REDACTED_CREDIT_CARD>" in scrubbed


def test_harness_patching_and_diff():
    harness = default_harness()
    orig_prompt_hash = harness.prompt_hash
    orig_config_hash = harness.config_hash

    # Apply patch: tighten truth guard and add Ashby fallback selector
    patch = {
        "selectors": {
            "ashby": {
                "location": ["input#_custom_ashby_location"]
            }
        },
        "answer_policies": {
            "truth_guard_strict": True,
            "security_clearance_default": "I do not have security clearance",
        },
    }

    patched = harness.apply_patch(patch)
    diff = harness.diff(patched)

    assert "selectors" in diff
    assert "input#_custom_ashby_location" in patched.selectors["ashby"]["location"]
    assert patched.config_hash != orig_config_hash


def test_tier1_truth_guard_circuit_breaker(in_memory_db):
    session, engine = in_memory_db
    runtime = ImprovementRuntime(engine=engine, session=session)

    # 1. Simulate run with truth guard violation (answering Yes to CPA without credential)
    with runtime.start_run(task_type="apply_greenhouse", inputs={"company": "Wealthsimple"}) as ctx:
        with ctx.span("verify_guardrail", "truth_guard") as span:
            span.record_error("TRUTH_GUARD_VIOLATION", "Answered Yes to CPA without verified credential")
        ctx.set_output({"submission_outcome": "success", "is_dry_run": True})

    eval_engine = EvaluationEngine(session=session)
    run = session.query(KernelRun).filter_by(id=ctx.run_id).first()
    assert run is not None

    res = eval_engine.evaluate_run(run)
    assert not res.passed
    assert not res.tier1_passed
    assert res.overall_score == 0.0
    assert "Hard circuit breaker tripped" in res.rationale
    assert run.failure_code == "SAFETY_UNSUPPORTED_QUALIFICATION"


def test_tier1_country_aware_location_verification(in_memory_db):
    session, engine = in_memory_db
    runtime = ImprovementRuntime(engine=engine, session=session)

    # Simulate run where location autocomplete selected Ontario, California instead of Canada
    with runtime.start_run(task_type="apply_ashby", inputs={"company": "Cohere"}) as ctx:
        with ctx.span("execute_tool", "fill_location_field") as span:
            span.record_error("COUNTRY_MISMATCH", "Selected US location Ontario, California, United States")
        ctx.set_output({"submission_outcome": "success", "is_dry_run": True})

    eval_engine = EvaluationEngine(session=session)
    run = session.query(KernelRun).filter_by(id=ctx.run_id).first()
    res = eval_engine.evaluate_run(run)

    assert not res.passed
    assert not res.tier1_passed
    assert res.overall_score == 0.0
    assert "COUNTRY_MISMATCH" in str(res.rationale) or "country mismatch" in str(res.rationale).lower()


def test_causal_analyzer_and_clustering(in_memory_db, tmp_path):
    session, engine = in_memory_db
    runtime = ImprovementRuntime(engine=engine, session=session)

    # Simulate run failing due to Ashby _systemfield_location selector missing
    with runtime.start_run(task_type="apply_ashby", inputs={"company": "Cohere"}) as ctx:
        with ctx.span("execute_tool", "fill_location") as span:
            span.record_error("SELECTOR_NOT_FOUND", "Unmatched fields: ['_systemfield_location']")
        ctx.fail_run("WORKFLOW_FORM_FILL_FAILED", "Failed on location input")

    replay_file = tmp_path / "replay_cases.jsonl"
    clusterer = DefectClusterer(session=session, project="hunterxjob", replay_cases_path=replay_file)
    cluster = clusterer.cluster_run(ctx.run_id)

    assert cluster.failure_category in (FailureCategory.TOOL, FailureCategory.WORKFLOW, FailureCategory.DATA)
    assert "DATA_UNMATCHED_FIELD" in cluster.pattern_signature or "fill_location" in cluster.pattern_signature
    assert cluster.occurrences_count == 1
    assert replay_file.exists()


def test_candidate_synthesizer_generates_declarative_patch(in_memory_db):
    session, engine = in_memory_db
    init_kernel_db(engine)

    # Seed defect cluster for truth guard violation
    cluster = DefectCluster(
        project="hunterxjob",
        failure_category=FailureCategory.SAFETY,
        failure_code="SAFETY_UNSUPPORTED_QUALIFICATION",
        root_cause_span="verify_guardrail:truth_guard",
        pattern_signature="SAFETY:SAFETY_UNSUPPORTED_QUALIFICATION:unsupported_credential",
        occurrences_count=3,
        status=DefectStatus.open.value,
    )
    session.add(cluster)
    session.commit()

    synthesizer = CandidateSynthesizer(session=session, project="hunterxjob")
    candidates = synthesizer.synthesize_all_open()

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.status == VersionStatus.candidate.value
    assert "challenger" in cand.version

    # Check that candidate harness has tightened answer policies
    harness = AgentHarness.from_dict(cand.harness_config)
    assert harness.answer_policies.get("truth_guard_strict") is True
    assert harness.answer_policies.get("require_verified_credential") is True


def test_statistical_promotion_gate_and_zero_regressions(in_memory_db):
    session, engine = in_memory_db
    init_kernel_db(engine)
    gate = PromotionGate(session=session, project="hunterxjob")

    # Case 1: Invariant failure -> Reject
    res_inv = gate.evaluate_challenger(
        champion_version="v2.0.0-champion",
        challenger_version="v2.0.1-challenger-broken",
        champion_scores=[0.90, 0.92, 0.88],
        challenger_scores=[0.95, 0.96, 0.94],
        candidate_invariants_passed=False,
    )
    assert res_inv.decision == PromotionDecision.reject
    assert not res_inv.invariants_passed

    # Case 2: Regression detected -> Reject
    res_reg = gate.evaluate_challenger(
        champion_version="v2.0.0-champion",
        challenger_version="v2.0.1-challenger-regression",
        champion_scores=[0.90, 0.85, 0.80, 0.88, 0.90],
        challenger_scores=[0.95, 0.50, 0.82, 0.90, 0.92],  # Case 2 dropped from 0.85 to 0.50
        candidate_invariants_passed=True,
    )
    assert res_reg.decision == PromotionDecision.reject
    assert res_reg.regressions_count > 0

    # Case 3: Statistically superior with zero regressions -> Auto-promote
    res_promo = gate.evaluate_challenger(
        champion_version="v2.0.0-champion",
        challenger_version="v2.0.1-challenger-win",
        champion_scores=[0.75, 0.78, 0.76, 0.74, 0.75, 0.77],
        challenger_scores=[0.92, 0.94, 0.91, 0.90, 0.93, 0.95],
        candidate_invariants_passed=True,
    )
    assert res_promo.decision == PromotionDecision.promote_auto
    assert res_promo.mean_delta > 0.10
    assert res_promo.regressions_count == 0


def test_sandbox_benchmark_execution(in_memory_db, tmp_path):
    session, engine = in_memory_db
    init_kernel_db(engine)
    champion = get_current_champion(session)

    # Create temporary golden dataset
    golden_file = tmp_path / "golden_test.jsonl"
    case = {
        "case_id": "golden-ashby-cohere-ottawa",
        "platform": "ashby",
        "company": "Cohere",
        "job_title": "Software Engineer",
        "job_location": "Ottawa, ON, Canada",
        "workplace_type": "hybrid",
        "candidate_profile": {
            "name": "Alex Mercer",
            "email": "alex.mercer@example.com",
            "phone": "+1-613-555-0199",
            "location": "Ottawa, Ontario, Canada",
            "certifications": [],
        },
        "expected_invariants": {
            "must_reject_application": False,
            "expected_location_country": "CA",
            "expected_cpa_answer": "No",
        },
    }
    golden_file.write_text(json.dumps(case) + "\n", encoding="utf-8")

    sandbox = SandboxRunner(session=session, project="hunterxjob")
    bench_res = sandbox.run_benchmark(champion, golden_file)

    assert bench_res.runs_count == 1
    assert bench_res.invariants_passed is True
    assert bench_res.mean_score >= 0.70


def test_end_to_end_continuous_improvement_cycle(in_memory_db, tmp_path):
    session, engine = in_memory_db
    init_kernel_db(engine)

    golden_file = tmp_path / "golden_suite.jsonl"
    case_good = {
        "case_id": "golden-ashby-cohere-ottawa",
        "platform": "ashby",
        "company": "Cohere",
        "job_title": "Software Engineer",
        "job_location": "Ottawa, ON, Canada",
        "workplace_type": "hybrid",
        "candidate_profile": {
            "name": "Alex Mercer",
            "email": "alex.mercer@example.com",
            "certifications": [],
        },
        "expected_invariants": {
            "must_reject_application": False,
            "expected_location_country": "CA",
            "expected_cpa_answer": "No",
        },
    }
    golden_file.write_text(json.dumps(case_good) + "\n", encoding="utf-8")
    replay_file = tmp_path / "replay_suite.jsonl"

    loop = ContinuousImprovementLoop(
        session=session,
        project="hunterxjob",
        golden_dataset_path=golden_file,
        replay_dataset_path=replay_file,
    )

    report = loop.run_cycle()
    assert report is not None
    assert isinstance(report.evaluated_runs, int)
    assert isinstance(report.new_clusters_count, int)


def test_allo_adapter_3_tier_real_trace_evaluation(in_memory_db):
    from improvement_kernel.adapters.allo import AlloAdapter

    session, engine = in_memory_db
    init_kernel_db(engine)

    adapter = AlloAdapter(session=session, project="allo")

    # 1. Valid trace passes all 3 tiers
    valid_trace = {
        "call_id": "test_call_success",
        "voice_name": "Aoede",
        "session_duration_ms": 35000,
        "connection_drops_count": 0,
        "turns": [
            {
                "turn_id": 1,
                "user_utterance": "Hey Kenza, how was your day?",
                "agent_response": "Hey babe! It was good, so happy you called. How are you feeling?",
                "speech_end_to_playback_ms": 550,
                "interrupt_to_playback_stop_ms": 110,
                "interrupted": False,
            }
        ],
    }
    res = adapter.evaluate_session_trace(valid_trace)
    assert res["overall_passed"] is True
    assert res["overall_score"] >= 0.80
    assert res["tier1"]["all_passed"] is True
    assert res["tier2"]["passed"] is True
    assert res["tier3"]["passed"] is True

    # 2. Forbidden male voice fails Tier 1
    male_voice_trace = {
        "call_id": "test_male_fail",
        "voice_name": "Puck",
        "session_duration_ms": 15000,
        "turns": [{"turn_id": 1, "agent_response": "Hello"}],
    }
    male_res = adapter.evaluate_session_trace(male_voice_trace)
    assert male_res["overall_passed"] is False
    assert male_res["overall_score"] == 0.0
    assert male_res["tier1"]["checks"]["voice_female_lock"]["passed"] is False

    # 3. Kingston repetition fails Tier 1
    kingston_trace = {
        "call_id": "test_kingston_fail",
        "voice_name": "Aoede",
        "turns": [{"turn_id": 1, "agent_response": "My sister in Kingston is doing great."}],
    }
    k_res = adapter.evaluate_session_trace(kingston_trace)
    assert k_res["overall_passed"] is False
    assert k_res["tier1"]["checks"]["banned_topics_clean"]["passed"] is False
