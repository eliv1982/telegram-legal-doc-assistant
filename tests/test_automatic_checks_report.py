"""
The user-facing automatic-checks section: written by code, placed before the model's assessment, bounded,
and never able to push the coverage line or the disclaimer out of the 4000-character Telegram message.
"""
from services.deterministic_checks import CheckStatus, run_deterministic_checks
from services.grounding import GroundedIssue
from services.report import (
    AUTOMATIC_FAILED,
    AUTOMATIC_HEADER,
    AUTOMATIC_NOTE,
    AUTOMATIC_OCR_NOTE,
    DISCLAIMER,
    MAX_CHECKS_CHARS,
    QUOTES_HEADER,
    REPORT_HEADER,
    compose_report,
    format_automatic_checks,
)
from tests.fakes import make_issue

COVERAGE = "ℹ️ Проанализированы только страницы 1–5 из 12. Страницы 6–12 в анализ не вошли."
LIMIT = 4000  # handlers.document.REPORT_CHAR_LIMIT


def finding(title: str, quote: str | None) -> GroundedIssue:
    return GroundedIssue(make_issue(title, evidence=quote, priority="high"), quote)


def test_the_section_lists_failures_first_names_the_pages_and_carries_one_note():
    text = "[Страница 1]\nКПП 773601001 ИНН 7707083893\n\n[Страница 2]\nИНН 7707083894"

    block = format_automatic_checks(run_deterministic_checks(text, analysed_pages=(1, 2)))

    assert block.splitlines() == [
        AUTOMATIC_HEADER,
        "❌ ИНН 7707083894 (стр. 2) — контрольное число не совпадает.",
        "✅ ИНН 7707083893 (стр. 1) — контрольное число корректно.",
        "ℹ️ КПП 773601001 (стр. 1) — формат соответствует ожидаемому; контрольная сумма для КПП не проверяется.",
        "",
        AUTOMATIC_NOTE,
    ]
    assert "не подтверждают существование или статус организации" in AUTOMATIC_NOTE
    assert AUTOMATIC_OCR_NOTE not in block and AUTOMATIC_OCR_NOTE in format_automatic_checks(
        run_deterministic_checks(text), ocr_used=True
    )


def test_no_labelled_requisites_means_no_section_and_an_internal_error_is_said_plainly():
    assert format_automatic_checks([]) == ""
    assert "не выполнены" in AUTOMATIC_FAILED and "не зависит" in AUTOMATIC_FAILED
    assert format_automatic_checks(None) == AUTOMATIC_FAILED


def test_the_section_is_bounded_and_says_how_many_checks_it_left_out():
    text = " ".join(f"ИНН 77070838{n:02d}" for n in range(60)) + " " + " ".join(f"КПП 77360{n:04d}" for n in range(60))
    checks = run_deterministic_checks(text)

    block = format_automatic_checks(checks)

    shown = [line for line in block.splitlines() if line.startswith(("❌", "✅", "ℹ"))]
    hidden_failed = sum(c.status is CheckStatus.FAIL for c in checks) - sum(line.startswith("❌") for line in shown)
    assert len(block) <= MAX_CHECKS_CHARS and 0 < len(shown) < len(checks)
    assert f"…и ещё проверок: {len(checks) - len(shown)}" in block
    assert shown[0].startswith("❌") and (f"из них не прошли: {hidden_failed}" in block) == bool(hidden_failed)


def test_the_automatic_section_comes_first_and_leaves_the_model_text_exactly_as_it_was():
    automatic = format_automatic_checks(run_deterministic_checks("ИНН 7707083894"))
    findings = [finding("Заголовок", "цитата из документа")]

    with_checks = compose_report("Текст модели", findings, COVERAGE, limit=LIMIT, automatic=automatic)
    without = compose_report("Текст модели", findings, COVERAGE, limit=LIMIT)

    assert with_checks == f"{automatic}\n\n{without}"  # the failed check changed nothing in the model's part
    assert with_checks.index(AUTOMATIC_HEADER) < with_checks.index(REPORT_HEADER) < with_checks.index("Текст модели")
    assert with_checks.index("Текст модели") < with_checks.index(QUOTES_HEADER) < with_checks.index(COVERAGE)
    assert with_checks.endswith(f"{COVERAGE}\n\n{DISCLAIMER}")


def test_the_worst_case_still_fits_telegram_with_coverage_and_disclaimer_intact():
    text = " ".join(f"ИНН 77070838{n:02d}" for n in range(80)) + " " + " ".join(f"КПП 77360{n:04d}" for n in range(80))
    automatic = format_automatic_checks(run_deterministic_checks(text), ocr_used=True)
    findings = [finding(f"Замечание {n}", "q" * 300 + str(n)) for n in range(40)] + [finding("Без цитаты", None)]

    report = compose_report("x" * 20_000, findings, COVERAGE, limit=LIMIT, automatic=automatic)

    assert len(report) <= LIMIT
    assert report.startswith(AUTOMATIC_HEADER) and automatic in report and AUTOMATIC_NOTE in report
    assert f"{REPORT_HEADER}\n\nx" in report  # the model still has room (and is the only part that was cut)
    assert report.endswith(f"{COVERAGE}\n\n{DISCLAIMER}")


def test_what_the_code_writes_claims_no_existence_and_shows_only_normalised_values():
    text = "ИНН 7707083893 ИНН 7707083894 ИНН 7707_083893 КПП 7701*1001 БИК [044525225] р/с `40702810300000012345`"

    block = format_automatic_checks(run_deterministic_checks(text))

    written_by_checks = block.replace(AUTOMATIC_NOTE, "").lower()  # the note itself denies these claims
    for banned in ("подтвержд", "существу", "действу", "достоверн", "проверен", "законн", "безопасн"):
        assert banned not in written_by_checks, banned
    assert not set("_*`[") & set(block)
