"""QAAnswerBank fuzzy matching should find the right stored answer for a paraphrase."""
from app.services.qa_bank import QAAnswerBank


def test_fuzzy_match_finds_paraphrased_question(db_session):
    bank = QAAnswerBank(db_session)
    bank.save_answer(
        pattern="Are you authorized to work in the United States?",
        answer="Yes, I am authorized to work in the US without sponsorship.",
    )
    bank.save_answer(
        pattern="How many years of experience do you have with Python?",
        answer="5 years of professional Python experience.",
    )

    result = bank.match_question("Are you legally authorized to work in the US?")
    assert result is not None
    assert "authorized to work" in result.answer_text.lower()


def test_no_match_below_threshold_returns_none(db_session):
    bank = QAAnswerBank(db_session, match_threshold=0.9)
    bank.save_answer(
        pattern="What is your desired salary?",
        answer="Negotiable based on total compensation.",
    )
    result = bank.match_question("Do you have a pet dinosaur?")
    assert result is None


def test_usage_count_increments_on_match(db_session):
    bank = QAAnswerBank(db_session)
    saved = bank.save_answer(
        pattern="Do you require visa sponsorship?",
        answer="No, I do not require visa sponsorship.",
    )
    assert saved.usage_count == 0

    bank.match_question("Do you require visa sponsorship now or in the future?")
    refreshed = bank.match_question("Will you require visa sponsorship?")
    assert refreshed is not None
    assert refreshed.usage_count >= 1


def test_save_answer_updates_near_identical_pattern(db_session):
    bank = QAAnswerBank(db_session)
    bank.save_answer(pattern="What is your notice period?", answer="Two weeks.")
    updated = bank.save_answer(pattern="What is your notice period?", answer="One month.")
    assert updated.answer_text == "One month."
