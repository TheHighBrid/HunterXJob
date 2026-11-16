"""Verification test suite for Fidelis Autonomous Improvement Integration.

Tests:
1. FidelisAdapter span tracing and run lifecycle.
2. Tier 1 Hard Invariants (strict dimension match, EXIF preservation, zero black tiles/NaNs).
3. Tier 2 Perceptual Quality Metrics (PSNR, SSIM, LPIPS).
4. Tier 3 Hallucination & Artifact Judge (plastic skin, over-sharpened ringing, mutated text).
5. Defect Clustering & Profile Routing Synthesizer (low-light portraits vs text graphics).
6. Golden Dataset integrity (20+ cases covering 5 domains).
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from improvement_kernel.adapters.fidelis import FidelisAdapter
from improvement_kernel.clustering.clusterer import cluster_run_failure
from improvement_kernel.evaluations.engine import grade_run
from improvement_kernel.optimizer.synthesizer import CandidateSynthesizer
from improvement_kernel.storage.db import get_current_champion, record_run
from improvement_kernel.storage.models import AgentVersion, DefectCluster, KernelRun, RunStatus, TraceSpan, VersionStatus


@pytest.fixture
def fidelis_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()

    # Seed baseline Fidelis champion
    champion = AgentVersion(
        project="fidelis",
        version="v2.0.0-champion",
        git_commit="c558ac8",
        prompt_hash="default_prompt",
        config_hash="default_config",
        harness_config_json=json.dumps({
            "routing_rules": {
                "default": {"model_profile": "Smart_Balanced", "tile_size": 512}
            },
            "tile_config": {"default_tile_size": 512, "overlap": 32}
        }),
        status=VersionStatus.champion.value,
        risk_level="low",
        notes="Seeded Fidelis champion baseline",
    )
    session.add(champion)
    session.commit()

    yield session, engine
    session.close()


def test_fidelis_golden_dataset_has_20_plus_cases():
    golden_path = Path(__file__).resolve().parents[1] / "evals" / "fidelis" / "golden_cases.jsonl"
    assert golden_path.is_file(), f"Golden cases file missing at {golden_path}"

    cases = []
    with open(golden_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))

    assert len(cases) >= 20, f"Expected at least 20 golden cases, found {len(cases)}"

    content_types = {c["content_type"] for c in cases}
    assert "portrait" in content_types, "Golden cases missing portrait domain"
    assert "anime" in content_types, "Golden cases missing anime domain"
    assert "text_document" in content_types, "Golden cases missing text document domain"
    assert "low_light" in content_types, "Golden cases missing low-light domain"
    assert "macro_texture" in content_types, "Golden cases missing macro texture domain"

    for c in cases:
        assert "case_id" in c
        assert c["task_type"] == "image_enhancement"
        assert "expected_invariants" in c
        inv = c["expected_invariants"]
        assert inv.get("strict_dimension_match") is True
        assert inv.get("min_psnr") is not None
        assert inv.get("min_ssim") is not None
        assert inv.get("zero_black_tiles") is True


def test_tier1_strict_dimension_mismatch_fails_closed(fidelis_db):
    session, _ = fidelis_db
    adapter = FidelisAdapter(session=session, project="fidelis")

    payload = {
        "image_id": "test_mismatch_dim",
        "input_dimensions": [256, 256],
        "output_dimensions": [1000, 1000],  # Expected 1024x1024 for 4x
        "scale": 4,
        "content_type": "photograph",
        "has_exif": False,
        "exif_preserved": True,
        "zero_black_tiles": True,
        "has_nans": False,
        "metrics": {"psnr": 32.0, "ssim": 0.95, "lpips": 0.10},
    }

    res = adapter.evaluate_restoration_trace(payload)
    assert res["passed"] is False
    assert res["overall_score"] == 0.0
    assert res["tier1"]["passed"] is False
    assert res["failure_code"] == "DATA_DIMENSION_MISMATCH"


def test_tier1_exif_metadata_dropped_fails_closed(fidelis_db):
    session, _ = fidelis_db
    adapter = FidelisAdapter(session=session, project="fidelis")

    payload = {
        "image_id": "test_exif_dropped",
        "input_dimensions": [256, 256],
        "output_dimensions": [1024, 1024],
        "scale": 4,
        "content_type": "photograph",
        "has_exif": True,
        "exif_preserved": False,  # EXIF was present but dropped
        "zero_black_tiles": True,
        "has_nans": False,
        "metrics": {"psnr": 32.0, "ssim": 0.95, "lpips": 0.10},
    }

    res = adapter.evaluate_restoration_trace(payload)
    assert res["passed"] is False
    assert res["overall_score"] == 0.0
    assert res["tier1"]["passed"] is False
    assert res["failure_code"] == "DATA_EXIF_CORRUPTED"


def test_tier1_black_tiles_or_nans_fails_closed(fidelis_db):
    session, _ = fidelis_db
    adapter = FidelisAdapter(session=session, project="fidelis")

    payload = {
        "image_id": "test_black_tile",
        "input_dimensions": [256, 256],
        "output_dimensions": [1024, 1024],
        "scale": 4,
        "content_type": "photograph",
        "has_exif": False,
        "exif_preserved": True,
        "zero_black_tiles": False,  # Dead black tile detected
        "has_nans": False,
        "metrics": {"psnr": 32.0, "ssim": 0.95, "lpips": 0.10},
    }

    res = adapter.evaluate_restoration_trace(payload)
    assert res["passed"] is False
    assert res["overall_score"] == 0.0
    assert res["tier1"]["passed"] is False
    assert res["failure_code"] == "TOOL_BLACK_TILE"


def test_tier2_perceptual_metrics_calculation(fidelis_db):
    session, _ = fidelis_db
    adapter = FidelisAdapter(session=session, project="fidelis")

    payload = {
        "image_id": "test_high_quality",
        "input_dimensions": [256, 256],
        "output_dimensions": [1024, 1024],
        "scale": 4,
        "content_type": "portrait",
        "has_exif": True,
        "exif_preserved": True,
        "zero_black_tiles": True,
        "has_nans": False,
        "metrics": {"psnr": 31.5, "ssim": 0.92, "lpips": 0.12},
        "detected_artifacts": [],
    }

    res = adapter.evaluate_restoration_trace(payload)
    assert res["passed"] is True
    assert res["tier1"]["passed"] is True
    assert res["tier2"]["passed"] is True
    assert res["tier2"]["composite_score"] >= 0.90
    assert res["overall_score"] >= 0.85


def test_tier3_plastic_skin_dollification_rejected(fidelis_db):
    session, _ = fidelis_db
    adapter = FidelisAdapter(session=session, project="fidelis")

    payload = {
        "image_id": "test_plastic_skin",
        "input_dimensions": [256, 256],
        "output_dimensions": [1024, 1024],
        "scale": 4,
        "content_type": "portrait",
        "has_exif": False,
        "exif_preserved": True,
        "zero_black_tiles": True,
        "has_nans": False,
        "metrics": {"psnr": 30.0, "ssim": 0.88, "lpips": 0.15},
        "detected_artifacts": ["plastic_skin"],
    }

    res = adapter.evaluate_restoration_trace(payload)
    assert res["passed"] is False
    assert res["failure_code"] == "MODEL_PLASTIC_SKIN"
    assert "plastic_skin" in res["tier3"]["detected_artifacts"]


def test_profile_routing_synthesizer_for_low_light_portrait(fidelis_db):
    session, _ = fidelis_db

    # Create a failed low-light portrait run
    run = KernelRun(
        id="run_lowlight_portrait_01",
        trace_id="trace_lowlight_01",
        project="fidelis",
        task_type="image_enhancement",
        agent_version="v2.0.0-champion",
        status=RunStatus.failed.value,
        failure_code="MODEL_PLASTIC_SKIN",
        error_message="Plastic skin detected on low light portrait with default profile",
        input_hash="hash_lowlight",
        output_hash="hash_out",
        latency_ms=1800,
    )
    session.add(run)

    span = TraceSpan(
        id="span_01",
        run_id=run.id,
        span_type="execute_tool",
        name="portrait_face_restoration",
        error_type="MODEL_PLASTIC_SKIN",
        error_message="Severe plastic skin dollification on low light portrait",
        input_json=json.dumps({"content_type": "low_light_portrait", "model_profile": "default"}),
        attributes_json=json.dumps({"content_type": "low_light_portrait"}),
    )
    session.add(span)
    session.commit()

    # 1. Cluster failure
    cluster = cluster_run_failure(session, run.id)
    assert cluster.failure_code == "MODEL_PLASTIC_SKIN"
    assert "low_light_portrait" in cluster.pattern_signature

    # 2. Synthesize challenger candidate profile
    synthesizer = CandidateSynthesizer(session=session, project="fidelis")
    candidate = synthesizer.synthesize_for_cluster(cluster)
    assert candidate is not None
    assert "challenger" in candidate.version
    assert candidate.status == VersionStatus.candidate.value

    # Check that the synthesized harness config routed low-light portraits to gfpgan_face with tile_size 256
    harness_config = json.loads(candidate.harness_config_json)
    assert "routing_rules" in harness_config
    rules = harness_config["routing_rules"]
    assert "low_light_portrait" in rules
    assert rules["low_light_portrait"]["model_profile"] == "gfpgan_face"
    assert rules["low_light_portrait"]["tile_size"] == 256
    assert rules["low_light_portrait"]["anti_dollification"] is True


def test_profile_routing_synthesizer_for_text_graphics(fidelis_db):
    session, _ = fidelis_db

    # Create a failed text graphics run
    run = KernelRun(
        id="run_text_graphics_01",
        trace_id="trace_text_01",
        project="fidelis",
        task_type="image_enhancement",
        agent_version="v2.0.0-champion",
        status=RunStatus.failed.value,
        failure_code="MODEL_MUTATED_TEXT",
        error_message="Mutated text glyphs on billboard text graphics with default profile",
        input_hash="hash_text",
        output_hash="hash_out",
        latency_ms=1400,
    )
    session.add(run)

    span = TraceSpan(
        id="span_02",
        run_id=run.id,
        span_type="execute_tool",
        name="text_restoration",
        error_type="MODEL_MUTATED_TEXT",
        error_message="Mutated text and distorted font glyphs on billboard signs",
        input_json=json.dumps({"content_type": "text_graphics", "model_profile": "default"}),
        attributes_json=json.dumps({"content_type": "text_graphics"}),
    )
    session.add(span)
    session.commit()

    # 1. Cluster failure
    cluster = cluster_run_failure(session, run.id)
    assert cluster.failure_code == "MODEL_MUTATED_TEXT"
    assert "text_graphics" in cluster.pattern_signature

    # 2. Synthesize candidate profile
    synthesizer = CandidateSynthesizer(session=session, project="fidelis")
    candidate = synthesizer.synthesize_for_cluster(cluster)
    assert candidate is not None

    harness_config = json.loads(candidate.harness_config_json)
    rules = harness_config["routing_rules"]
    assert "text_graphics" in rules
    assert rules["text_graphics"]["model_profile"] == "d2fk-v1"
    assert rules["text_graphics"]["tile_size"] == 512
    assert rules["text_graphics"]["anti_ringing_filter"] is True
