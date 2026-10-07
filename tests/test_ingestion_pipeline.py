"""
Ingestion through the handlers and the pipeline (Stage 3): invalid input fails locally and for free, in either
upload order; valid input proceeds in the order validate -> extract -> transcribe -> analyse; every successful
report carries a coverage line written by code. Offline: OpenAI and Telegram are fakes (or the real SDK on a
scripted transport), Poppler is stubbed except where a test says otherwise.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pdf2image.exceptions import PDFInfoNotInstalledError

from handlers.document import (
    AI_FAILURE_MESSAGES,
    ALLOWED_DOC_EXTENSIONS,
    CHECKLIST_CAPTION,
    DOWNLOAD_ERROR_MSG,
    ERROR_MSG,
    REJECTION_MESSAGES,
    REPORT_CHAR_LIMIT,
    WAIT_DOC_MSG,
    WAIT_VOICE_MSG,
)
from services import limits, pdf_converter
from services.ai_errors import AIFailure, AIServiceError
from services.openai_service import OpenAIService
from services.pdf_converter import RenderedPage, RenderError
from services.report import DISCLAIMER, REPORT_HEADER
from services.validation import Rejection, validate_document
from states.user_states import PENDING_FILE_KEY, WORKSPACE_KEY
from tests import samples
from tests.fakes import FakeBot, FakeMessage, FakeOpenAIService, FakeTTSService, ScriptedOpenAI, make_report

WAITING_FOR_DOCUMENT = "UserSessionState:waiting_for_document"
WAITING_FOR_VOICE = "UserSessionState:waiting_for_voice"

# (file name as the user sent it, its bytes, why it must be refused)
INVALID_DOCUMENTS = [
    pytest.param("empty.pdf", b"", Rejection.EMPTY, id="empty"),
    pytest.param("fake.pdf", b"just some text pretending to be a pdf", Rejection.UNSUPPORTED_TYPE, id="text-renamed-pdf"),
    pytest.param("corrupt.pdf", b"%PDF-1.4\ncorrupt body", Rejection.CORRUPT_PDF, id="corrupt-pdf"),
    pytest.param("locked.pdf", samples.encrypted_pdf(), Rejection.ENCRYPTED_PDF, id="encrypted-pdf"),
    pytest.param("long.pdf", samples.blank_pdf(limits.MAX_PDF_PAGES + 1), Rejection.TOO_MANY_PAGES, id="too-many-pages"),
    pytest.param("hostile.pdf", samples.pdf_with_page_box(200_000, 200_000), Rejection.PAGE_SIZE, id="giant-mediabox"),
    pytest.param("cut.png", samples.png_bytes()[:60], Rejection.UNREADABLE_IMAGE, id="truncated-image"),
    pytest.param("bomb.png", samples.png_declaring(60_000, 60_000), Rejection.IMAGE_SIZE, id="decompression-bomb-image"),
]


@pytest.fixture
def paid(handler_env):
    """The OpenAI/TTS fakes the handlers receive. Their call records are the proof of what was paid for."""
    return SimpleNamespace(openai=handler_env.ai.openai, tts=handler_env.ai.tts)


def nothing_paid(paid) -> bool:
    return paid.openai.calls == [] and paid.tts.calls == 0


async def send(env, kind: str, state, unique_id: str = "x", *, name: str = "contract.png", data: bytes | None = None,
               **message_args) -> FakeMessage:
    """Deliver a voice or a document to its handler; `data` is what Telegram 'serves' for that file."""
    if kind == "voice":
        message, handler = FakeMessage.with_voice(unique_id, **message_args), env.document.handle_voice
        remote = f"remote/voice-{unique_id}"
    else:
        message = FakeMessage.with_document(file_name=name, unique_id=unique_id, **message_args)
        handler, remote = env.document.handle_document, f"remote/doc-{unique_id}"
    if data is not None:
        env.bot.payloads[remote] = data
    await handler(message, state, env.bot, env.ai)
    return message


def report_of(env) -> str:
    return env.bot.sent_texts[-1]


def full_report(body: str, coverage: str) -> str:
    """What the user receives: code header, the model's text, the code coverage line, the code disclaimer."""
    return f"{REPORT_HEADER}\n\n{body}\n\nℹ️ {coverage}\n\n{DISCLAIMER}"


# --- invalid documents: zero paid calls, whichever message arrives first ------------------------------------------


@pytest.mark.parametrize("name, data, reason", INVALID_DOCUMENTS)
async def test_an_invalid_document_sent_first_is_refused_at_once_and_nothing_is_paid_for(handler_env, paid, name, data, reason):
    env = handler_env
    state = env.new_state()

    message = await send(env, "document", state, "bad", name=name, data=data)

    assert message.answers == [REJECTION_MESSAGES[reason]]
    assert await state.get_state() is None  # no session was opened and no voice is awaited
    assert env.leftover_files() == []
    voice = await send(env, "voice", state)  # the voice that follows starts a fresh session, it does not pair with the bad file
    assert voice.answers == [WAIT_DOC_MSG]
    assert env.pipeline_calls == [] and nothing_paid(paid)


@pytest.mark.parametrize("name, data, reason", INVALID_DOCUMENTS)
async def test_an_invalid_document_sent_after_the_voice_keeps_the_voice_and_the_session(handler_env, paid, name, data, reason):
    env = handler_env
    state = env.new_state()
    await send(env, "voice", state)
    workspace = Path((await state.get_data())[WORKSPACE_KEY])

    message = await send(env, "document", state, "bad", name=name, data=data)

    assert message.answers == [REJECTION_MESSAGES[reason]]
    assert await state.get_state() == WAITING_FOR_DOCUMENT
    assert [p.name for p in workspace.iterdir()] == ["voice.ogg"]  # the refused file is gone, the voice is kept
    assert env.pipeline_calls == [] and nothing_paid(paid)

    await send(env, "document", state, "good")  # a valid document completes the very same session
    assert env.pipeline_calls == [1] and paid.openai.calls  # paid calls happen only now
    assert env.leftover_files() == []


async def test_an_invalid_replacement_does_not_discard_the_pending_valid_document(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state, "good")
    pending = await state.get_data()

    message = await send(env, "document", state, "bad", name="corrupt.pdf", data=b"%PDF-1.4\ncorrupt")

    assert message.answers == [REJECTION_MESSAGES[Rejection.CORRUPT_PDF]]
    assert await state.get_data() == pending and await state.get_state() == WAITING_FOR_VOICE
    assert env.leftover_files() == [Path(pending[WORKSPACE_KEY]).name]  # the refused download left nothing behind

    await send(env, "voice", state)
    assert Path(env.pipeline_args[0][3]) == Path(pending[PENDING_FILE_KEY])  # the original document was analysed


async def test_a_refused_voice_does_not_replace_or_open_anything(handler_env, paid):
    env = handler_env
    state = env.new_state()

    message = await send(env, "voice", state, "empty", data=b"")

    assert message.answers == [REJECTION_MESSAGES[Rejection.EMPTY]]
    assert await state.get_state() is None and env.leftover_files() == [] and nothing_paid(paid)


@pytest.mark.parametrize("name, data, reason", INVALID_DOCUMENTS)
async def test_the_pipeline_alone_refuses_invalid_documents_before_any_openai_call(
    handler_env, tmp_path, name, data, reason
):
    """run_pipeline does not rely on the upload-time check. Real OpenAIService on the real SDK: count what would be billed."""
    openai = ScriptedOpenAI()  # any request would raise: nothing is scripted
    tts, bot = FakeTTSService(), FakeBot()
    (tmp_path / "voice.ogg").write_bytes(b"OggS-voice")
    (tmp_path / "doc.bin").write_bytes(data)

    await handler_env.document.run_pipeline(
        bot, 1, tmp_path / "voice.ogg", tmp_path / "doc.bin", OpenAIService(openai.client), tts, "pdf"
    )

    assert openai.requests == [] and tts.calls == 0
    assert REJECTION_MESSAGES[reason] in bot.sent_texts
    assert ERROR_MSG not in bot.sent_texts  # a permanent input problem is not reported as a transient failure
    assert bot.sent_voices == [] and bot.sent_documents == []


async def test_an_empty_voice_is_refused_before_vision_is_paid_for(handler_env, tmp_path):
    openai = ScriptedOpenAI()
    bot = FakeBot()
    (tmp_path / "voice.ogg").write_bytes(b"")
    (tmp_path / "scan.png").write_bytes(samples.png_bytes())

    await handler_env.document.run_pipeline(
        bot, 1, tmp_path / "voice.ogg", tmp_path / "scan.png", OpenAIService(openai.client), FakeTTSService(), "pdf"
    )

    assert openai.requests == []  # neither Vision nor Whisper
    assert REJECTION_MESSAGES[Rejection.EMPTY] in bot.sent_texts


async def test_permanent_rejections_are_distinct_from_transient_failures(handler_env, paid, monkeypatch):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state, "doc")

    async def openai_is_down(self, image_bytes, mime_type="image/jpeg"):
        raise RuntimeError("503 from OpenAI")

    monkeypatch.setattr(FakeOpenAIService, "extract_text_from_image", openai_is_down)
    await send(env, "voice", state)

    assert ERROR_MSG in env.bot.sent_texts  # transient: "try later" is the right advice
    assert not set(REJECTION_MESSAGES.values()) & set(env.bot.sent_texts)
    assert env.leftover_files() == []


# --- valid documents proceed, in the promised order --------------------------------------------------------------


async def test_a_valid_image_goes_validate_then_vision_then_whisper_then_analysis(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)

    await send(env, "voice", state)

    assert paid.openai.calls == [
        "extract_text_from_image", "transcribe_voice", "analyze_document", "generate_report",
    ]
    assert paid.openai.ocr_images[0].startswith(b"\xff\xd8\xff")  # the normalized JPEG, never the raw upload


async def test_a_born_digital_pdf_is_never_sent_to_vision(handler_env, paid, monkeypatch):
    env = handler_env
    monkeypatch.setattr(pdf_converter, "render_pages", lambda *args: pytest.fail("a text PDF must not be rendered"))
    state = env.new_state()

    await send(env, "voice", state)
    await send(env, "document", state, name="contract.pdf", data=samples.text_pdf(2))

    assert paid.openai.calls == ["transcribe_voice", "analyze_document", "generate_report"]
    assert "CLAUSE-1" in paid.openai.analysis_inputs[0] and "CLAUSE-2" in paid.openai.analysis_inputs[0]


async def test_a_scanned_pdf_is_read_by_vision_before_the_voice_is_transcribed(handler_env, paid, monkeypatch):
    env = handler_env
    monkeypatch.setattr(
        pdf_converter, "render_pages", lambda path, pages: {n: RenderedPage(b"\xff\xd8\xffscan", False) for n in pages}
    )
    state = env.new_state()
    await send(env, "document", state, name="scan.pdf", data=samples.pdf_bytes([None, None]))

    await send(env, "voice", state)

    assert paid.openai.calls[:3] == ["extract_text_from_image", "extract_text_from_image", "transcribe_voice"]


async def test_the_file_extension_is_not_trusted_in_either_direction(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state, "a", name="scan.pdf", data=samples.png_bytes())  # a PNG named .pdf
    await send(env, "voice", state, "a")
    assert paid.openai.calls[0] == "extract_text_from_image"  # processed as the image it is

    paid.openai.calls.clear()
    await send(env, "document", state, "b", name="photo.png", data=samples.text_pdf(1))  # a PDF named .png
    await send(env, "voice", state, "b")
    assert paid.openai.calls[0] == "transcribe_voice"  # processed as a text PDF: no Vision


# --- rendering problems are permanent, local and free ------------------------------------------------------------


@pytest.mark.parametrize("reason", [Rejection.RENDER_FAILED, Rejection.RENDER_TIMEOUT])
async def test_a_render_problem_costs_nothing_not_even_the_transcription(handler_env, paid, monkeypatch, reason):
    env = handler_env

    def failing_render(path, pages):
        raise RenderError(reason)

    monkeypatch.setattr(pdf_converter, "render_pages", failing_render)
    state = env.new_state()
    await send(env, "document", state, name="scan.pdf", data=samples.pdf_bytes([samples.page_text(1), None]))

    await send(env, "voice", state)

    assert nothing_paid(paid)
    assert REJECTION_MESSAGES[reason] in env.bot.sent_texts and ERROR_MSG not in env.bot.sent_texts
    assert env.leftover_files() == [] and await state.get_state() is None


async def test_poppler_missing_is_reported_as_a_service_problem_not_as_a_bad_file(handler_env, paid, monkeypatch):
    env = handler_env

    def no_poppler(path, pages):
        raise PDFInfoNotInstalledError("Is poppler installed and in PATH?")

    monkeypatch.setattr(pdf_converter, "render_pages", no_poppler)
    state = env.new_state()
    await send(env, "document", state, name="scan.pdf", data=samples.pdf_bytes([None]))

    await send(env, "voice", state)

    assert nothing_paid(paid)
    assert ERROR_MSG in env.bot.sent_texts and not set(REJECTION_MESSAGES.values()) & set(env.bot.sent_texts)


async def test_a_blank_pdf_is_refused_without_paying_for_vision(handler_env, paid, monkeypatch):
    env = handler_env
    monkeypatch.setattr(pdf_converter, "render_pages", lambda path, pages: {n: RenderedPage(b"jpeg", True) for n in pages})
    state = env.new_state()
    await send(env, "document", state, name="blank.pdf", data=samples.blank_pdf(2))

    await send(env, "voice", state)

    assert nothing_paid(paid)
    assert REJECTION_MESSAGES[Rejection.NO_TEXT] in env.bot.sent_texts


# --- coverage disclosure ------------------------------------------------------------------------------------------


async def test_the_report_for_an_image_says_the_uploaded_image_was_analysed(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    await send(env, "voice", state)

    assert report_of(env) == full_report("Отчёт", "Проанализировано: загруженное изображение.")
    assert env.bot.document_captions == [CHECKLIST_CAPTION]  # the checklist file carries the model-assessment note, too


async def test_the_report_for_a_long_pdf_states_exactly_which_pages_were_analysed(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state, name="long.pdf", data=samples.text_pdf(12))
    await send(env, "voice", state)

    assert report_of(env) == full_report(
        "Отчёт", "Проанализированы только страницы 1–5 из 12. Страницы 6–12 в анализ не вошли."
    )
    analysed = paid.openai.analysis_inputs[0]
    assert "CLAUSE-5" in analysed and "CLAUSE-6" not in analysed  # what the report claims is what the model saw


async def test_a_pdf_within_budget_reports_all_its_pages(handler_env, paid):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state, name="short.pdf", data=samples.text_pdf(3))
    await send(env, "voice", state)

    assert "ℹ️ Проанализированные страницы: 1–3 из 3." in report_of(env)


async def test_the_coverage_line_and_the_disclaimer_cannot_be_cut_off_by_a_long_model_report(handler_env, paid, monkeypatch):
    env = handler_env

    async def endless_report(self, **kwargs):
        return make_report("x" * 10_000)

    monkeypatch.setattr(FakeOpenAIService, "generate_report", endless_report)
    state = env.new_state()
    await send(env, "document", state, name="long.pdf", data=samples.text_pdf(12))
    await send(env, "voice", state)

    report = report_of(env)
    assert len(report) <= REPORT_CHAR_LIMIT
    assert report.startswith(REPORT_HEADER)
    assert report.endswith(f"ℹ️ Проанализированы только страницы 1–5 из 12. Страницы 6–12 в анализ не вошли.\n\n{DISCLAIMER}")


async def test_a_model_service_failure_sends_only_its_message_never_a_report_or_a_coverage_line(handler_env, paid, monkeypatch):
    env = handler_env

    async def invalid_output(self, voice_transcript, document_text):
        raise AIServiceError(AIFailure.INVALID_OUTPUT, "ValidationError")

    monkeypatch.setattr(FakeOpenAIService, "analyze_document", invalid_output)
    state = env.new_state()
    await send(env, "document", state)
    await send(env, "voice", state)

    assert AI_FAILURE_MESSAGES[AIFailure.INVALID_OUTPUT] in env.bot.sent_texts
    assert not any("ℹ️" in text or DISCLAIMER in text for text in env.bot.sent_texts)
    assert env.bot.sent_voices == [] and env.bot.sent_documents == []
    assert env.leftover_files() == []


# --- before the download --------------------------------------------------------------------------------------------


@pytest.fixture
def downloads(handler_env):
    seen: list[str] = []

    async def record(file_path: str) -> None:
        seen.append(file_path)

    handler_env.bot.before_download = record
    return seen


async def test_a_file_declared_too_large_is_refused_without_downloading_it(handler_env, paid, downloads):
    env = handler_env
    state = env.new_state()

    message = await send(env, "document", state, file_size=limits.MAX_UPLOAD_BYTES + 1)

    assert message.answers == [REJECTION_MESSAGES[Rejection.TOO_LARGE]]
    assert downloads == [] and env.leftover_files() == [] and await state.get_state() is None


async def test_a_voice_declared_too_large_is_refused_without_downloading_it(handler_env, paid, downloads):
    env = handler_env
    state = env.new_state()

    message = await send(env, "voice", state, file_size=limits.MAX_UPLOAD_BYTES + 1)

    assert message.answers == [REJECTION_MESSAGES[Rejection.TOO_LARGE]]
    assert downloads == [] and env.leftover_files() == []


@pytest.mark.parametrize("name", ["contract.docx", "archive.zip", "notes.txt", ""])
async def test_an_obviously_unsupported_file_is_refused_without_downloading_it(handler_env, paid, downloads, name):
    env = handler_env
    state = env.new_state()

    message = await send(env, "document", state, name=name)

    assert message.answers == [REJECTION_MESSAGES[Rejection.UNSUPPORTED_TYPE]]
    assert downloads == [] and env.leftover_files() == []


@pytest.mark.parametrize(
    "name, mime, suffix",
    [("report", "application/pdf", ".pdf"), (None, "image/png", ".png"), ("scan.JPG", None, ".jpg"), ("x.docx", "image/webp", ".webp")],
)
async def test_the_declared_mime_type_stands_in_for_a_missing_extension(handler_env, paid, downloads, name, mime, suffix):
    env = handler_env
    state = env.new_state()

    message = await send(env, "document", state, name=name, mime_type=mime)

    assert message.answers == [WAIT_VOICE_MSG]
    assert downloads == ["remote/doc-x"]
    workspace = Path((await state.get_data())[WORKSPACE_KEY])
    assert [p.name for p in workspace.iterdir()] == [f"document{suffix}"]


# --- user-facing messages ------------------------------------------------------------------------------------------


def test_every_rejection_has_a_message_and_none_of_them_says_try_later():
    assert set(REJECTION_MESSAGES) == set(Rejection)
    for reason, text in REJECTION_MESSAGES.items():
        assert "позже" not in text.lower(), reason  # the file is the problem: retrying the same file cannot help
        assert text not in (ERROR_MSG, DOWNLOAD_ERROR_MSG) and len(text) < 260, reason
    assert "позже" in ERROR_MSG  # the transient message keeps its advice
    assert len(set(REJECTION_MESSAGES.values())) == len(REJECTION_MESSAGES)  # each class is distinguishable


def test_the_messages_name_the_limits_that_are_actually_configured():
    assert str(limits.MAX_PDF_PAGES) in REJECTION_MESSAGES[Rejection.TOO_MANY_PAGES]
    assert str(limits.MAX_UPLOAD_BYTES // (1024 * 1024)) in REJECTION_MESSAGES[Rejection.TOO_LARGE]
    assert str(limits.MAX_IMAGE_PIXELS // 1_000_000) in REJECTION_MESSAGES[Rejection.IMAGE_SIZE]


def test_the_supported_extensions_are_the_documented_ones():
    assert ALLOWED_DOC_EXTENSIONS == {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


# --- the hostile fixture from the audit ----------------------------------------------------------------------------


async def test_a_tiny_pdf_with_absurd_page_dimensions_is_refused_by_preflight_before_any_poppler_work(
    handler_env, tmp_path, monkeypatch
):
    """
    The audit's case: a few hundred bytes that declare a 200000 x 200000 pt page. This does not reproduce the
    resource exhaustion; it proves the preflight limit stops the file before rendering, Poppler or OpenAI are reached.
    """
    hostile = samples.pdf_with_page_box(200_000, 200_000)
    assert len(hostile) < 1000

    def must_not_run(*args, **kwargs):
        pytest.fail("a hostile PDF reached the renderer")

    for name in ("convert_from_path", "pdfinfo_from_path", "render_pages", "extract_page_texts"):
        monkeypatch.setattr(pdf_converter, name, must_not_run)
    monkeypatch.setattr(subprocess, "Popen", must_not_run)

    openai = ScriptedOpenAI()
    bot = FakeBot()
    (tmp_path / "voice.ogg").write_bytes(b"OggS-voice")
    (tmp_path / "hostile.pdf").write_bytes(hostile)

    assert validate_document(tmp_path / "hostile.pdf") is Rejection.PAGE_SIZE
    await handler_env.document.run_pipeline(
        bot, 1, tmp_path / "voice.ogg", tmp_path / "hostile.pdf", OpenAIService(openai.client), FakeTTSService(), "pdf"
    )

    assert REJECTION_MESSAGES[Rejection.PAGE_SIZE] in bot.sent_texts
    assert openai.requests == []
