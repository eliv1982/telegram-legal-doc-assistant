"""
Shape of the prompt templates: they are str.format() templates full of literal JSON braces, so a stray
brace edit breaks them at runtime (KeyError/ValueError), not at import time.
"""
from prompts.analysis_prompt import ANALYSIS_PROMPT, RISK_SCALE
from prompts.response_prompt import RESPONSE_PROMPT, RISK_SCALE_RESPONSE


def test_prompt_templates_format_cleanly():
    analysis = ANALYSIS_PROMPT.format(
        voice_transcript="Проверь договор",
        document_text="Текст договора",
        risk_scale=RISK_SCALE,
    )
    assert "Проверь договор" in analysis
    assert '"confidence": 85' in analysis  # escaped JSON braces survive formatting
    assert "{{" not in analysis

    response = RESPONSE_PROMPT.format(
        analysis_json="{}",
        document_type="Договор",
        user_task="Анализ рисков",
        issues_list="[]",
        RISK_SCALE_RESPONSE=RISK_SCALE_RESPONSE,
    )
    for marker in (
        "=== TEXT_REPORT ===",
        "=== END_TEXT_REPORT ===",
        "=== TTS_SCRIPT ===",
        "=== END_TTS_SCRIPT ===",
        "=== CHECKLIST ===",
        "=== END_CHECKLIST ===",
    ):
        assert marker in response
