"""
Logging privacy: normal logs carry stages, classes and sizes, never transcript/document text,
and a redaction filter masks bot tokens / API keys as a second line of defence.
"""
import io
import logging

import pytest

from handlers.document import ERROR_MSG
from services.ai_errors import AIFailure, AIServiceError
from services.openai_service import OpenAIService
from services.schemas import AnalysisResult, ChecklistItem, Issue, ReportResult
from tests.fakes import FAKE_BOT_TOKEN, FakeMessage, FakeOpenAIService, ScriptedOpenAI, chat_response
from utils.logging_config import REDACTED, RedactSecretsFilter, setup_logging

FAKE_OPENAI_KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def make_record(msg: str, *args, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("test", logging.ERROR, __file__, 1, msg, args, exc_info)


def test_redaction_masks_tokens_and_keys_everywhere_a_record_carries_them():
    log_filter = RedactSecretsFilter()
    url = f"https://api.telegram.org/file/bot{FAKE_BOT_TOKEN}/voice/file_1.oga"

    # in the message text, in the %-arguments, and for an OpenAI key
    for record in (make_record(f"GET {url} failed"), make_record("GET %s failed", url), make_record(f"key {FAKE_OPENAI_KEY}")):
        assert log_filter.filter(record) is True
        assert FAKE_BOT_TOKEN not in record.getMessage() and FAKE_OPENAI_KEY not in record.getMessage()
        assert REDACTED in record.getMessage()

    # in a traceback, as rendered by a handler (what logger.exception would print)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(log_filter)
    logger = logging.getLogger("test.redaction.traceback")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise RuntimeError(f"404, url='{url}'")
        except RuntimeError:
            logger.exception("download crashed")
    finally:
        logger.removeHandler(handler)
    assert "download crashed" in stream.getvalue() and "RuntimeError" in stream.getvalue()
    assert FAKE_BOT_TOKEN not in stream.getvalue()

    # ordinary text is left alone, and a malformed record cannot make the filter raise
    plain = make_record("Transcribed: %d chars at 12:30:45", 120)
    log_filter.filter(plain)
    assert plain.getMessage() == "Transcribed: 120 chars at 12:30:45"
    assert log_filter.filter(make_record("needs two args: %s %s", "only one")) is True


def test_setup_logging_puts_the_filter_on_every_handler(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))

    setup_logging(log_file=str(tmp_path / "bot.log"))

    handlers = captured["handlers"]
    try:
        assert len(handlers) == 2  # stdout + file
        assert all(any(isinstance(f, RedactSecretsFilter) for f in h.filters) for h in handlers)
    finally:
        for handler in handlers:
            handler.close()


class SentinelOpenAIService(FakeOpenAIService):
    """Every piece of user/document-derived text is a recognisable sentinel."""

    async def transcribe_voice(self, audio_path) -> str:
        return "SENTINEL-TRANSCRIPT"

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return "SENTINEL-DOCUMENT-TEXT"

    async def analyze_document(self, voice_transcript: str, document_text: str) -> AnalysisResult:
        return AnalysisResult(
            document_type="SENTINEL-DOC-TYPE",
            summary="SENTINEL-SUMMARY",
            key_facts=[],
            issues=[Issue(priority="low", title="SENTINEL-ISSUE", description="SENTINEL-DESC", evidence="SENTINEL-QUOTE")],
        )

    async def generate_report(self, **kwargs) -> ReportResult:
        return ReportResult(
            text_report="SENTINEL-REPORT", tts_script="SENTINEL-TTS",
            checklist_items=[ChecklistItem(priority="low", text="SENTINEL-CHECKLIST")],
        )


async def test_pipeline_logs_contain_no_user_or_document_derived_text(handler_env, monkeypatch, caplog):
    env = handler_env
    env.ai.openai = SentinelOpenAIService()
    caplog.set_level(logging.DEBUG)
    state = env.new_state()

    async def run_session(unique_id: str) -> None:
        await env.document.handle_document(FakeMessage.with_document(unique_id=unique_id), state, env.bot, env.ai)
        await env.document.handle_voice(FakeMessage.with_voice(unique_id), state, env.bot, env.ai)

    await run_session("ok")  # a successful run
    assert len(env.bot.sent_voices) == 1

    async def leaky_failure(self, **kwargs):  # an exception whose text contains document content
        raise RuntimeError("SENTINEL-IN-EXCEPTION-TEXT SENTINEL-DOCUMENT-TEXT")

    monkeypatch.setattr(SentinelOpenAIService, "generate_report", leaky_failure)
    await run_session("failing")  # and a failing one
    assert ERROR_MSG in env.bot.sent_texts

    assert "pipeline failed" in caplog.text and "RuntimeError" in caplog.text  # what failed is still visible
    assert "SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    "reply, failure",
    [
        pytest.param(chat_response("SENTINEL-MODEL-OUTPUT, certainly not JSON"), AIFailure.INVALID_OUTPUT, id="prose"),
        pytest.param(chat_response('{"document_type": "SENTINEL-DOC", "summary": 5}'), AIFailure.INVALID_OUTPUT, id="wrong-schema"),
        pytest.param(chat_response(None, refusal="SENTINEL-REFUSAL-TEXT"), AIFailure.REFUSED, id="refusal"),
    ],
)
async def test_a_failed_model_response_is_neither_logged_nor_carried_in_the_error(reply, failure, caplog):
    """Pydantic's own ValidationError text quotes the model output; the adapter must not let it out."""
    caplog.set_level(logging.DEBUG)
    openai = ScriptedOpenAI(reply)

    with pytest.raises(AIServiceError) as raised:
        await OpenAIService(openai.client).analyze_document("task", "document")

    assert raised.value.kind is failure
    assert "SENTINEL" not in caplog.text and "SENTINEL" not in repr(raised.value) and "SENTINEL" not in str(raised.value)
    error = raised.value
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)  # nothing chained to print later
