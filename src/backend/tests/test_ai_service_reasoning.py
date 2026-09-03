import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_service import _reasoning_effort_for_question


def test_uses_low_reasoning_for_simple_definition_with_gpt5():
    assert _reasoning_effort_for_question(
        "Que es On Shore Power Supply segun la IEC 80005-1?",
        "gpt-5.4-nano",
    ) == "low"


def test_keeps_default_reasoning_for_complex_technical_questions():
    assert _reasoning_effort_for_question(
        "Cada cuanto se limpian los evaporadores segun tabla 3.1 RITE?",
        "gpt-5.4-nano",
    ) is None


def test_does_not_pass_reasoning_effort_to_non_gpt5_fallback_model():
    assert _reasoning_effort_for_question(
        "Que es On Shore Power Supply segun la IEC 80005-1?",
        "gpt-4.1-mini",
    ) is None
