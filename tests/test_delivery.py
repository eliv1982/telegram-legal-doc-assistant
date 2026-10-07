"""
Delivery: the text report is the primary deliverable and goes out first; the voice summary and the PDF
checklist come after it and are optional, each on its own. Offline: Telegram and OpenAI are fakes, the PDF renderer is real.
"""
import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from bot import create_bot
from handlers.document import (
    AI_FAILURE_MESSAGES,
    CHECKLIST_CAPTION,
    CHECKLIST_FAILED_MSG,
    ERROR_MSG,
    VOICE_FAILED_MSG,
)
from services.ai_errors import AIFailure, AIServiceError
from services.checklist_generator import AUTOMATIC_TITLE, MODEL_TITLE
from services.report import REPORT_HEADER, TTS_AUTOMATIC_FAILED, TTS_DISCLAIMER
from services.schemas import ChecklistItem
from tests.fakes import FakeMessage, FakeOpenAIService, FakeStatusMessage, make_report

ROOT = Path(__file__).resolve().parent.parent
PROVIDER_TEXT = "SENTINEL-PROVIDER-TEXT"


async def run_session(env) -> None:
    """Send the document, then the voice that completes the session."""
    state = env.new_state()
    await env.document.handle_document(FakeMessage.with_document(unique_id="d"), state, env.bot, env.ai)
    await env.document.handle_voice(FakeMessage.with_voice("v"), state, env.bot, env.ai)
    assert await state.get_state() is None and env.leftover_files() == []  # the session is closed and its files are gone


def report_texts(env) -> list[str]:
    return [text for text in env.bot.sent_texts if REPORT_HEADER in text]


def assert_report_delivered_and_nothing_failed_loudly(env) -> None:
    assert len(report_texts(env)) == 1
    assert ERROR_MSG not in env.bot.sent_texts and not set(AI_FAILURE_MESSAGES.values()) & set(env.bot.sent_texts)
    assert PROVIDER_TEXT not in "".join(env.bot.sent_texts)


async def boom(*args, **kwargs):
    raise RuntimeError(PROVIDER_TEXT)


def renderer_boom(*args, **kwargs):  # generate_checklist is synchronous: it runs in a worker thread
    raise RuntimeError(PROVIDER_TEXT)


def pdf_text(document) -> str:
    return " ".join(" ".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(document.data)).pages)


async def test_the_report_goes_out_before_either_optional_file_is_even_attempted(handler_env, monkeypatch):
    env = handler_env
    events: list[str] = []
    real_send_message = env.bot.send_message

    async def send_message(chat_id, text, **kwargs):
        events.append("report" if REPORT_HEADER in text else "status")
        return await real_send_message(chat_id, text, **kwargs)

    async def speak(text):
        events.append("synthesis")
        return b"ID3placeholder"

    async def send_voice(chat_id, voice, **kwargs):
        events.append("voice")

    async def send_document(chat_id, document, **kwargs):
        events.append("checklist")

    monkeypatch.setattr(env.bot, "send_message", send_message)
    monkeypatch.setattr(env.ai.tts, "text_to_speech", speak)
    monkeypatch.setattr(env.bot, "send_voice", send_voice)
    monkeypatch.setattr(env.bot, "send_document", send_document)

    await run_session(env)

    assert [event for event in events if event != "status"] == ["report", "synthesis", "voice", "checklist"]
    assert_report_delivered_and_nothing_failed_loudly(env)


@pytest.mark.parametrize("break_voice", ["synthesis-fails", "telegram-refuses-the-voice"])
async def test_a_voice_failure_costs_only_the_voice_and_says_so_once(handler_env, monkeypatch, break_voice):
    env = handler_env
    if break_voice == "synthesis-fails":
        async def refused(text):
            raise AIServiceError(AIFailure.UNAVAILABLE, PROVIDER_TEXT)

        monkeypatch.setattr(env.ai.tts, "text_to_speech", refused)
    else:
        monkeypatch.setattr(env.bot, "send_voice", boom)

    await run_session(env)

    assert_report_delivered_and_nothing_failed_loudly(env)
    assert env.bot.sent_texts.count(VOICE_FAILED_MSG) == 1 and CHECKLIST_FAILED_MSG not in env.bot.sent_texts
    assert not env.bot.sent_voices and len(env.bot.sent_documents) == 1  # the checklist still arrived


async def test_a_checklist_failure_keeps_the_report_and_the_voice_that_was_already_sent(handler_env, monkeypatch):
    env = handler_env
    monkeypatch.setattr(env.document, "generate_checklist", renderer_boom)

    await run_session(env)

    assert_report_delivered_and_nothing_failed_loudly(env)
    assert env.bot.sent_texts.count(CHECKLIST_FAILED_MSG) == 1 and VOICE_FAILED_MSG not in env.bot.sent_texts
    assert len(env.bot.sent_voices) == 1 and not env.bot.sent_documents


async def test_both_optional_files_failing_leave_the_report_and_one_notice_each(handler_env, monkeypatch):
    env = handler_env
    monkeypatch.setattr(env.ai.tts, "text_to_speech", boom)
    monkeypatch.setattr(env.document, "generate_checklist", renderer_boom)

    await run_session(env)

    assert_report_delivered_and_nothing_failed_loudly(env)
    assert [text for text in env.bot.sent_texts if text in (VOICE_FAILED_MSG, CHECKLIST_FAILED_MSG)] == [
        VOICE_FAILED_MSG, CHECKLIST_FAILED_MSG,
    ]
    assert not env.bot.sent_voices and not env.bot.sent_documents


async def test_a_failing_notice_or_status_message_cannot_turn_into_a_failed_request(handler_env, monkeypatch):
    env = handler_env
    real_send_message = env.bot.send_message

    async def send_message(chat_id, text, **kwargs):
        if text == VOICE_FAILED_MSG:
            raise RuntimeError("Telegram is down")  # even the one-line notice cannot be delivered
        return await real_send_message(chat_id, text, **kwargs)

    monkeypatch.setattr(env.bot, "send_message", send_message)
    monkeypatch.setattr(env.ai.tts, "text_to_speech", boom)
    monkeypatch.setattr(FakeStatusMessage, "edit_text", boom)  # the "processing" status is decoration, too
    monkeypatch.setattr(FakeStatusMessage, "delete", boom)

    await run_session(env)

    assert_report_delivered_and_nothing_failed_loudly(env)
    assert len(env.bot.sent_documents) == 1  # the checklist was not skipped because of the voice and the notice


async def test_when_the_report_itself_cannot_be_sent_nothing_else_is_attempted(handler_env, monkeypatch):
    env = handler_env
    real_send_message = env.bot.send_message

    async def send_message(chat_id, text, **kwargs):
        if REPORT_HEADER in text:
            raise RuntimeError(PROVIDER_TEXT)
        return await real_send_message(chat_id, text, **kwargs)

    monkeypatch.setattr(env.bot, "send_message", send_message)

    await run_session(env)

    assert ERROR_MSG in env.bot.sent_texts and PROVIDER_TEXT not in "".join(env.bot.sent_texts)
    assert env.ai.tts.calls == 0 and not env.bot.sent_voices and not env.bot.sent_documents


@pytest.mark.parametrize(
    "document_text, check_failed",
    [("Продавец: ИНН 7707083894.", True), ("Продавец: ИНН 7707083893.", False)],
)
async def test_the_pdf_lists_a_failed_check_apart_from_the_model_items_and_the_voice_only_mentions_it(
    handler_env, monkeypatch, document_text, check_failed
):
    env = handler_env

    async def vision(self, image_bytes, mime_type="image/jpeg"):
        return document_text

    async def report(self, *, task, analysis):
        script = "Это договор поставки. Главный риск — пеня. Согласуйте предел."
        return make_report("Модель: замечаний нет.", script, ChecklistItem(priority="high", text="Согласовать предел пени"))

    monkeypatch.setattr(FakeOpenAIService, "extract_text_from_image", vision)
    monkeypatch.setattr(FakeOpenAIService, "generate_report", report)

    await run_session(env)

    checklist, voice = env.bot.sent_documents[0], env.bot.sent_voices[0]
    text = pdf_text(checklist)
    assert (checklist.filename, voice.filename) == ("checklist.pdf", "resume.mp3")
    assert env.bot.document_captions == [CHECKLIST_CAPTION] and "не юридическая консультация" in CHECKLIST_CAPTION
    assert MODEL_TITLE in text and "[Высокий] Согласовать предел пени" in text
    assert (AUTOMATIC_TITLE in text) is check_failed and ("ИНН 7707083894" in text) is check_failed
    assert "7707083893" not in text  # a passing check never reaches the checklist
    spoken = env.ai.tts.texts[0]
    expected = "Это договор поставки. Главный риск — пеня. Согласуйте предел."
    assert spoken == " ".join([expected, *([TTS_AUTOMATIC_FAILED] if check_failed else []), TTS_DISCLAIMER])
    assert "7707083893" not in spoken and "7707083894" not in spoken  # the checks themselves are never read aloud


# --- Telegram formatting ---------------------------------------------------------------------------------------------


async def test_arbitrary_model_text_goes_out_once_verbatim_and_without_a_markup_mode(handler_env, monkeypatch):
    env = handler_env
    tricky = (
        "_a_ *b* [c](http://evil.example) `d` <b>e</b> <a href=\"http://evil.example\">f</a> &amp; # x \\ ~~s~~ "
        "||spoiler|| «ё» 🚀 @channel /start"
    )

    async def report(self, *, task, analysis):
        return make_report(tricky)

    monkeypatch.setattr(FakeOpenAIService, "generate_report", report)

    await run_session(env)

    sent = [(text, kwargs) for text, kwargs in zip(env.bot.sent_texts, env.bot.sent_kwargs) if REPORT_HEADER in text]
    assert len(sent) == 1  # one send: no "that failed, retry it in plain text" branch left
    text, kwargs = sent[0]
    assert tricky in text
    assert all(not {"parse_mode", "entities"} & set(kwargs) for kwargs in env.bot.sent_kwargs)  # every message is plain text


def test_the_bot_is_built_without_a_markup_mode_and_with_link_previews_off():
    bot = create_bot("123456789:AAFakeTokenForTests-abcdefghijklmnop_QRS")

    assert bot.default.parse_mode is None and bot.default.link_preview_is_disabled is True


def test_no_runtime_code_selects_a_telegram_markup_mode():
    runtime_files = [ROOT / "bot.py", *(path for directory in ("handlers", "services") for path in (ROOT / directory).rglob("*.py"))]
    assert any(path.name == "document.py" for path in runtime_files)  # the scan is not vacuous
    for path in runtime_files:
        text = path.read_text(encoding="utf-8").lower()
        assert "parse_mode" not in text and "parsemode" not in text, path.name
