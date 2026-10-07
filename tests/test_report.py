"""
Report composition (plain text): what code adds around the model's text, that none of it can be
shortened away, and how the spoken summary is bounded.
"""
from services import limits
from services.grounding import GroundedIssue
from services.report import (
    DISCLAIMER,
    QUOTES_HEADER,
    REPORT_HEADER,
    SHORTENED_MARKER,
    TTS_AUTOMATIC_FAILED,
    TTS_DISCLAIMER,
    compose_report,
    compose_tts_script,
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
    assert text.startswith(f"{REPORT_HEADER}\n\nx") and SHORTENED_MARKER in text  # the model's text was cut, and says so
    assert QUOTES_HEADER in text and "…и ещё цитат:" in text  # the quote block is bounded, and says so
    assert "Для 1 из 41 замечаний" in text
    assert text.endswith(f"{COVERAGE}\n\n{DISCLAIMER}")  # footer intact, last, and not cut


def test_an_oversized_model_text_is_shortened_by_paragraphs_not_by_characters():
    paragraphs = [f"Абзац {n}. " + " ".join(["Слово"] * 40) for n in range(60)]
    model_text = "\n\n".join(paragraphs)

    text = compose_report(model_text, [finding("Заголовок", "цитата")], COVERAGE, limit=LIMIT)

    body = text.split(f"{REPORT_HEADER}\n\n", 1)[1].split(f"\n\n{QUOTES_HEADER}")[0]
    assert len(text) <= LIMIT and body.endswith(SHORTENED_MARKER)
    kept = body.removesuffix(SHORTENED_MARKER).split("\n\n")
    assert 0 < len(kept) < len(paragraphs) and kept == paragraphs[: len(kept)]  # whole paragraphs, in order, none torn
    assert text.endswith(f"{COVERAGE}\n\n{DISCLAIMER}")


def test_model_and_document_text_goes_out_verbatim_there_is_nothing_to_escape():
    tricky = "a_b *c* [d](http://x.y) `e` <b>f</b> & <i> «ё» 🚀 _"
    text = compose_report(tricky, [finding(tricky, tricky)], COVERAGE, limit=LIMIT)

    assert tricky in text.split(REPORT_HEADER)[1].split("\n\n")[1]  # the model's body
    assert f"• [Высокий] {tricky}: «{tricky}»" in text  # the quote line: title and quote, unescaped


SCRIPT = (
    "Это договор аренды нежилого помещения. Главный риск — неограниченная неустойка по пункту 4.2. "
    "Рекомендуем согласовать предельный размер пени до подписания. Аренда составляет 120 тысяч рублей в месяц."
)


def test_the_spoken_summary_is_bounded_by_the_word_budget_and_never_cut_inside_a_word(monkeypatch):
    long_script = " ".join(f"Предложение номер {n} о договоре и его условиях." for n in range(200))

    spoken = compose_tts_script(long_script)

    body = spoken.removesuffix(f" {TTS_DISCLAIMER}")
    assert len(body.split()) <= limits.TTS_MAX_WORDS and body.endswith(".")
    assert body == " ".join(f"Предложение номер {n} о договоре и его условиях." for n in range(len(body.split()) // 8))
    for budget in (3, 5, 9, 14, 30):  # a tight budget cuts at a sentence end whenever one fits, at a whole word otherwise
        monkeypatch.setattr(limits, "TTS_MAX_WORDS", budget)
        trimmed = compose_tts_script(SCRIPT).removesuffix(f" {TTS_DISCLAIMER}")
        assert len(trimmed.split()) <= budget and trimmed[-1] in ".!?…"
        assert set(trimmed.replace(".", "").split()) <= set(SCRIPT.replace(".", "").split())


def test_the_spoken_summary_always_says_it_is_preliminary_and_mentions_failed_checks_in_one_short_phrase():
    plain = compose_tts_script(SCRIPT)
    flagged = compose_tts_script(SCRIPT, automatic_failed=True)

    assert plain == f"{SCRIPT} {TTS_DISCLAIMER}"
    assert flagged == f"{SCRIPT} {TTS_AUTOMATIC_FAILED} {TTS_DISCLAIMER}"
    assert TTS_AUTOMATIC_FAILED == "Автоматические проверки реквизитов выявили замечания."
    assert "ИНН" not in flagged and "контрольное число" not in flagged  # the checks themselves are not read out
    assert compose_tts_script("Без точки в конце").startswith("Без точки в конце. ")  # the notes start a new sentence


def test_the_api_character_limit_is_a_last_resort_that_still_ends_on_a_word(monkeypatch):
    monkeypatch.setattr(limits, "TTS_INPUT_LIMIT", 120)

    spoken = compose_tts_script(SCRIPT)

    assert len(spoken) <= 120 and spoken.endswith(TTS_DISCLAIMER)
    body = spoken.removesuffix(f" {TTS_DISCLAIMER}")
    assert set(body.replace(".", "").split()) <= set(SCRIPT.replace(".", "").split())
