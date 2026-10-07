"""
Automatic checks inside the pipeline: they run on the extracted document text only, after extraction and
before anything is paid for, and the model's assessment neither feeds them nor depends on them. Offline: fakes only.
"""
from services import pdf_converter
from services.report import (
    AUTOMATIC_FAILED,
    AUTOMATIC_HEADER,
    AUTOMATIC_OCR_NOTE,
    DISCLAIMER,
    REPORT_HEADER,
)
from services.schemas import AnalysisResult, KeyFact
from tests import samples
from tests.fakes import FakeMessage, FakeOpenAIService, make_report

# Valid, and claimed by the model as a key fact, but absent from the document text.
MODEL_ONLY_INN = "7707329152"


async def run_session(env, *, name: str = "contract.png", data: bytes | None = None) -> str:
    """Send the document, then the voice that completes the session; return the report the user received."""
    state = env.new_state()
    if data is not None:
        env.bot.payloads["remote/doc-d"] = data
    await env.document.handle_document(FakeMessage.with_document(file_name=name, unique_id="d"), state, env.bot, env.ai)
    await env.document.handle_voice(FakeMessage.with_voice("v"), state, env.bot, env.ai)
    return env.bot.sent_texts[-1]


def vision_reads(monkeypatch, text: str) -> None:
    async def ocr(self, image_bytes, mime_type="image/jpeg"):
        self.calls.append("extract_text_from_image")
        self.ocr_images.append(image_bytes)
        return text

    monkeypatch.setattr(FakeOpenAIService, "extract_text_from_image", ocr)


async def test_a_failed_check_and_a_confident_model_are_separate_sections_and_neither_changes_the_other(
    handler_env, monkeypatch
):
    env = handler_env
    vision_reads(monkeypatch, "Договор.\nПродавец: ИНН 7707083894. Покупатель: ИНН 7707083893, КПП 773601001.")

    async def confident_analysis(self, voice_transcript, document_text):
        self.calls.append("analyze_document")
        self.analysis_inputs.append(document_text)
        facts = [KeyFact(name="ИНН продавца", value=f"{MODEL_ONLY_INN}, проверен, корректен")]
        return AnalysisResult(document_type="Договор", summary="Суть.", key_facts=facts, issues=[])

    async def confident_report(self, *, task, analysis):
        self.calls.append("generate_report")
        return make_report("Модель: все ИНН корректны, документ безопасен.")

    monkeypatch.setattr(FakeOpenAIService, "analyze_document", confident_analysis)
    monkeypatch.setattr(FakeOpenAIService, "generate_report", confident_report)

    report = await run_session(env)

    automatic, model = report.split(REPORT_HEADER)
    assert automatic.startswith(AUTOMATIC_HEADER) and AUTOMATIC_OCR_NOTE in automatic  # the text came from Vision
    assert "❌ ИНН 7707083894" in automatic and "✅ ИНН 7707083893" in automatic and "ℹ️ КПП 773601001" in automatic
    assert MODEL_ONLY_INN not in automatic  # the model's key fact never became a check
    assert "Модель: все ИНН корректны, документ безопасен." in model and "Модель" not in automatic
    assert report.endswith(f"ℹ️ Проанализировано: загруженное изображение.\n\n{DISCLAIMER}")
    ai = env.ai.openai
    assert ai.calls == ["extract_text_from_image", "transcribe_voice", "analyze_document", "generate_report"]  # no extra AI call
    assert AUTOMATIC_HEADER not in ai.analysis_inputs[0] and "контрольное число" not in ai.analysis_inputs[0]
    assert env.bot.sent_voices and env.bot.sent_documents  # the failed check blocked neither the voice nor the checklist


async def test_an_internal_error_in_the_checks_does_not_cost_the_user_the_model_assessment(handler_env, monkeypatch):
    env = handler_env

    def crash(*args, **kwargs):
        raise RuntimeError("programming error")

    monkeypatch.setattr(env.document, "run_deterministic_checks", crash)

    report = await run_session(env)

    assert report.startswith(AUTOMATIC_FAILED) and REPORT_HEADER in report and report.endswith(DISCLAIMER)
    assert env.document.ERROR_MSG not in env.bot.sent_texts
    assert env.ai.openai.calls[-2:] == ["analyze_document", "generate_report"] and env.bot.sent_voices


async def test_requisites_are_attributed_to_analysed_pages_only(handler_env, monkeypatch):
    env = handler_env
    pages = {n: f"Страница договора {n}. Условия поставки и оплаты, ответственность сторон." for n in range(1, 13)}
    pages[2] += " ИНН 7707083893"
    pages[7] += " ИНН 7707083894"  # beyond the 5 analysed pages: nobody read it, so nothing is checked in it
    monkeypatch.setattr(pdf_converter, "extract_page_texts", lambda path, wanted: {n: pages[n] for n in wanted})

    report = await run_session(env, name="long.pdf", data=samples.text_pdf(12))

    automatic = report.split(REPORT_HEADER)[0]
    assert "✅ ИНН 7707083893 (стр. 2) —" in automatic and AUTOMATIC_OCR_NOTE not in automatic  # text layer, no Vision
    assert "7707083894" not in report and "Страницы 6–12 в анализ не вошли" in report


async def test_many_requisites_and_a_long_model_report_still_fit_with_coverage_and_disclaimer_last(handler_env, monkeypatch):
    env = handler_env
    vision_reads(monkeypatch, " ".join(f"ИНН 77070838{n:02d} КПП 77360{n:04d}" for n in range(100)))

    async def endless_report(self, **kwargs):
        return make_report("x" * 10_000)

    monkeypatch.setattr(FakeOpenAIService, "generate_report", endless_report)

    report = await run_session(env)

    assert len(report) <= env.document.REPORT_CHAR_LIMIT
    assert report.startswith(AUTOMATIC_HEADER) and f"{REPORT_HEADER}\n\nx" in report
    assert report.endswith(f"ℹ️ Проанализировано: загруженное изображение.\n\n{DISCLAIMER}")
