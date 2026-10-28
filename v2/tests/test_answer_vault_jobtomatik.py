import pytest
from app.answer_policy_compiler import load_master_policy, get_seed_records
from app.question_catalog import classify_question
from app.knowledge_book import KnowledgeBook
from app.vault_store import load_vault, seed_answer_vault_from_policy
from app.db import SessionLocal, init_db


@pytest.fixture
def db_session():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_master_answer_policy_compiles_correctly():
    policy = load_master_policy(force_reload=True)
    assert policy.schema_version == "3.0"
    assert policy.project == "HunterXJob"
    assert len(policy.items_list) >= 140
    assert "salary_expectation" in policy.items_by_key
    assert "start_availability" in policy.items_by_key
    assert "currently_employed_any" in policy.items_by_key
    assert "relocation" in policy.items_by_key

    # Check targeting rules
    assert policy.targeting.get("currency") == "CAD"
    assert policy.targeting.get("salary_baseline_annual") == 60000
    assert "TD" in policy.targeting.get("employer_exclusions", [])


def test_question_matching_multilingual_en_and_fr():
    policy = load_master_policy()

    # 1. Salary in English & French
    item_en, s_en = policy.find_match("What are your salary expectations?")
    assert item_en is not None and item_en.key == "salary_expectation"
    assert item_en.answer == "60000"

    item_fr, s_fr = policy.find_match("Prétentions salariales?")
    assert item_fr is not None and item_fr.key == "salary_expectation"
    assert item_fr.answer == "60000"

    # 2. Employment status (Self-employed founder of MELATO)
    item_emp, _ = policy.find_match("Are you currently employed?")
    assert item_emp is not None and item_emp.key == "currently_employed_any"
    assert item_emp.answer == "Self-employed"

    item_elsewhere, _ = policy.find_match("Are you presently employed by another organization?")
    assert item_elsewhere is not None and item_elsewhere.key == "currently_employed_elsewhere"
    assert item_elsewhere.answer == "No"

    # 3. Relocation & Commute
    item_relo, _ = policy.find_match("Are you willing to relocate?")
    assert item_relo is not None and item_relo.key == "relocation"
    assert item_relo.answer == "No"

    item_local, _ = policy.find_match("Are you able to work on-site in Ottawa?")
    assert item_local is not None and item_local.key == "onsite_or_hybrid_local"
    assert item_local.answer == "Yes"

    item_nonlocal, _ = policy.find_match("Can you work in person in Toronto?")
    assert item_nonlocal is not None and item_nonlocal.key == "onsite_or_hybrid_nonlocal"
    assert item_nonlocal.answer == "No"


def test_skills_and_narratives_from_policy():
    policy = load_master_policy()

    # Skills
    excel = policy.get("excel_skill")
    assert excel is not None and excel.answer == "Good"

    netsuite = policy.get("netsuite_skill")
    assert netsuite is not None and netsuite.answer == "Very good"

    salesforce = policy.get("salesforce_skill")
    assert salesforce is not None and salesforce.answer == "Intermediate"

    # Narrative question definitions in policy
    why_co = policy.get("why_company")
    assert why_co is not None
    assert why_co.category == "free_text"
    assert "banking" in why_co.notes

    supervision = policy.get("supervision_example")
    assert supervision is not None
    assert "RBC" in supervision.answer
    assert "accomplishments" in supervision.notes


def test_classify_question_bridges_to_master_policy():
    # Question from master policy not originally in basic catalog
    res = classify_question("Are you a current employee of another company?")
    assert res is not None
    assert res["canonical_key"] == "currently_employed_elsewhere"
    assert "policy_item" in res


def test_knowledge_book_resolves_master_policy_answers():
    from pathlib import Path
    example_yaml = Path(__file__).parent.parent / "examples" / "profile.example.yaml"
    kb = KnowledgeBook.from_yaml_file(example_yaml)
    kb.preferences["willing_to_relocate"] = False

    # 1. Salary expectation
    ans, rule = kb.resolve_screening_question("What are your salary expectations?", options=["60,000", "75,000", "90,000"])
    assert ans == "60,000"

    # 2. Employment status
    ans, rule = kb.resolve_screening_question(
        "Are you currently employed?",
        options=["Employed", "Unemployed", "Self-employed"],
    )
    assert ans == "Self-employed"

    # 3. Relocation
    ans, rule = kb.resolve_screening_question("Are you willing to relocate?", options=["Yes", "No"])
    assert ans == "No"

    # 4. Notice period / Immediate availability
    ans, rule = kb.resolve_screening_question(
        "When can you start?",
        options=["Immediately", "In 2 weeks", "In 1 month"],
    )
    assert ans == "Immediately"


def test_seed_answer_vault_and_load(db_session):
    result = seed_answer_vault_from_policy(db_session, force=True)
    assert result["total"] >= 50
    assert result["json_seeded"] >= 15

    vault = load_vault(db_session, scope="global")
    assert vault.has("salary_expectation")
    assert vault.get("salary_expectation").value == "60000"
    assert vault.has("current_company")
    assert vault.get("current_company").value == "MELATO"
    assert vault.has("willing_to_relocate")
    assert vault.get("willing_to_relocate").value == "No"
