"""
Report composition (Stage 4): what code adds around the model's text, and that none of it can be shortened away.
"""
from services.grounding import GroundedIssue
from services.report import (
    DISCLAIMER,
    ELLIPSIS,
    QUOTES_HEADER,
    REPORT_HEADER,
    TTS_DISCLAIMER,
    compose_report,
    compose_tts_script,
    escape_markdown,
)
from tests.fakes import make_issue

COVERAGE = "ℹ️ Проанализированы только страницы 1–5 из 12. Страницы 6–12 в анализ не вошли."
LIMIT = 4000  # handlers.document.REPORT_CHAR_LIMIT


def finding(title: str, quote: str | None, priority="high") -> GroundedIssue:
    return GroundedIssue(make_issue(title, evidence=quote, priority=priority), quote)


def test_the_report_is_header_model_text_quotes_coverage_and_disclaimer_in_that_order():
    findings = [finding("Заголовок", "цитата из документа"), finding("Без цитаты", None, "low")]

    text = compose_report("Текст модели", findings, COVERAGE, limit=LIMIT)

    assert text == "\n\n".join([
        REPORT_HEADER,
        "Текст модели",
        f"{QUOTES_HEADER}\n• [Высокий] Заголовок: «цитата из документа»\n"
        "Для 1 из 2 замечаний нет подтверждающей цитаты из текста документа: это оценка модели.",
        COVERAGE,
        DISCLAIMER,
    ])
    assert "оценка модели" in REPORT_HEADER and "не юридическая консультация" in DISCLAIMER


def test_no_findings_is_a_valid_result_with_no_quote_block_and_no_claim_of_safety():
    text = compose_report("В проанализированной части замечаний не выявлено.", [], COVERAGE, limit=LIMIT)

    assert text == "\n\n".join([REPORT_HEADER, "В проанализированной части замечаний не выявлено.", COVERAGE, DISCLAIMER])
    assert QUOTES_HEADER not in text


def test_only_the_model_text_is_shortened_everything_the_code_adds_survives():
    findings = [finding(f"Замечание {n}", "q" * 300 + str(n)) for n in range(40)] + [finding("Без цитаты", None)]

    text = compose_report("x" * 20_000, findings, COVERAGE, limit=LIMIT)

    assert len(text) <= LIMIT
    assert text.startswith(f"{REPORT_HEADER}\n\nx") and ELLIPSIS in text  # the model's text was cut
    assert QUOTES_HEADER in text and "…и ещё цитат:" in text  # the quote block is bounded, and says so
    assert "Для 1 из 41 замечаний" in text
    assert text.endswith(f"{COVERAGE}\n\n{DISCLAIMER}")  # footer intact, last, and not cut


def test_quotes_cannot_break_the_telegram_markdown_of_the_message():
    assert escape_markdown("a_b *c* [d] `e` f") == "a\\_b \\*c\\* \\[d] \\`e\\` f"
    text = compose_report("т", [finding("Загол_овок", "цитата с_подчёркиванием *и* звёздочками")], COVERAGE, limit=LIMIT)
    assert "Загол\\_овок: «цитата с\\_подчёркиванием \\*и\\* звёздочками»" in text


def test_the_voice_script_always_carries_the_preliminary_assessment_note():
    assert compose_tts_script("Резюме анализа.", limit=4096) == f"Резюме анализа. {TTS_DISCLAIMER}"

    long_script = compose_tts_script("слово " * 2000, limit=4096)

    assert len(long_script) <= 4096 and long_script.endswith(TTS_DISCLAIMER)
