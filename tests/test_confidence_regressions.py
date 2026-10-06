"""
Regression ledger: confirmed confidence-semantics defect (Stage 4/5).

Each test states the DESIRED behavior and is a strict xfail because the code does not do that yet.
When the defect is fixed the test XPASSes, strict mode turns that red, and the marker has to be
removed together with the fix. `raises=AssertionError` keeps a broken harness from hiding as an xfail.
"""
import pytest

import config
from handlers.document import LOW_CONFIDENCE_MSG, run_pipeline
from services import openai_service
from services.openai_service import OpenAIService
from tests.fakes import FakeBot, FakeOpenAIClient, FakeTTSService
from utils.helpers import parse_confidence

confidence_defect = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Stage 4/5: confirmed confidence semantics defect",
)


@confidence_defect
def test_missing_confidence_does_not_pass_the_threshold():
    """No confidence from the model must fail closed. Today parse_confidence(None) is 100 (fails open)."""
    assert parse_confidence(None) < config.CONFIDENCE_THRESHOLD


@confidence_defect
def test_garbled_confidence_does_not_pass_the_threshold():
    """Unparseable confidence must fail closed. Today every one of these is 100 (ValueError -> 100)."""
    for garbled in ("n/a", "high", "", "approximately ninety"):
        assert parse_confidence(garbled) < config.CONFIDENCE_THRESHOLD, garbled


@confidence_defect
def test_fractional_confidence_is_read_as_a_percentage():
    """
    A model answering on a 0-1 scale means 95 % by 0.95. Today int(0.95) == 0, so a good document
    is rejected as unreadable. The exact normalization policy is a Stage 4/5 decision; collapsing to 0 is not.
    """
    assert parse_confidence(0.95) == 95
    assert parse_confidence("0.95") == 95


@confidence_defect
async def test_malformed_model_output_is_not_reported_as_low_document_quality(tmp_path, monkeypatch):
    """
    The analysis model replies with prose instead of JSON. That is a model/output-format failure, not a
    blurry scan, so the user must not be told "document quality is low". Today analyze_document substitutes
    confidence "0%" and run_pipeline sends LOW_CONFIDENCE_MSG.
    """
    client = FakeOpenAIClient(chat_replies=["document text read by vision", "Sorry, I cannot answer in JSON."])
    monkeypatch.setattr(openai_service, "AsyncOpenAI", lambda api_key=None: client)
    service = OpenAIService(api_key="offline-test-key")

    voice_path = tmp_path / "voice.ogg"
    doc_path = tmp_path / "doc.png"
    voice_path.write_bytes(b"placeholder")
    doc_path.write_bytes(b"placeholder")
    bot = FakeBot()

    await run_pipeline(bot, 1, voice_path, doc_path, service, FakeTTSService(), "gtts", "pdf")

    if client.chat_calls != 2:  # harness check, deliberately not an AssertionError
        pytest.fail(f"expected OCR + analysis calls, got {client.chat_calls}")
    assert LOW_CONFIDENCE_MSG not in bot.sent_texts
