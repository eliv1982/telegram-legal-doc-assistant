"""
Regression ledger: the confidence-semantics defect (fixed).

The model used to rate its own OCR quality as a number and the pipeline gated on it: a missing or garbage value became
100, `0.95` became 0, and a model that answered in prose was reported as "the document quality is low". The four
strict xfails that recorded this are now passing tests of the new semantics: the number is gone, extraction success is
decided by the deterministic extraction layer, and a model/format failure is a service outcome, never a verdict on the file.
"""
import re
from pathlib import Path

import pytest

import config
from handlers import document
from handlers.document import AI_FAILURE_MESSAGES, ERROR_MSG, run_pipeline
from services.ai_errors import AIFailure
from services.openai_service import OpenAIService
from tests import samples
from tests.fakes import (
    FakeBot,
    FakeTTSService,
    ScriptedOpenAI,
    chat_response,
    make_analysis,
    make_report,
    structured_response,
    transcription_response,
)

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_CODE_DIRS = ("handlers", "prompts", "services", "states", "utils")

# Identifiers of the retired mechanisms: JSON salvage, confidence gate, marker-delimited report.
RETIRED = (
    r"parse_confidence", r"CONFIDENCE_THRESHOLD", r"LOW_CONFIDENCE", r"extract_json", r"parse_response_sections",
    r"=== ", r"\bmax_tokens\b",
)


async def run_with(tmp_path, *replies) -> FakeBot:
    """A PNG upload through the REAL OpenAIService and SDK: replies are Vision, Whisper, analysis, report (in order)."""
    openai = ScriptedOpenAI(*replies)
    voice_path, doc_path = tmp_path / "voice.ogg", tmp_path / "doc.png"
    voice_path.write_bytes(b"placeholder")
    doc_path.write_bytes(samples.png_bytes())
    bot = FakeBot()
    await run_pipeline(bot, 1, voice_path, doc_path, OpenAIService(openai.client), FakeTTSService())
    return bot


def vision_and_whisper():
    return chat_response("document text read by vision"), transcription_response()


def test_the_numeric_confidence_machinery_and_its_siblings_are_gone():
    """Was: missing / garbled / fractional confidence gave the wrong number. There is no number now, nor the code around it."""
    assert not hasattr(config, "CONFIDENCE_THRESHOLD") and not hasattr(document, "LOW_CONFIDENCE_MSG")
    assert not (ROOT / "utils" / "helpers.py").exists()

    scanned = [ROOT / "bot.py", ROOT / "config.py"] + [p for d in RUNTIME_CODE_DIRS for p in (ROOT / d).rglob("*.py")]
    assert len(scanned) > 10  # the scan is not vacuous
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for pattern in RETIRED:
            assert not re.search(pattern, text), f"{path.name}: {pattern}"
    assert "confidence" not in (ROOT / ".env.example").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("legacy", [None, "n/a", 0.95, 5], ids=["absent", "garbage", "fraction", "low-number"])
async def test_a_legacy_confidence_field_cannot_make_a_request_succeed_or_fail(tmp_path, legacy):
    """The same analysis, with whatever `confidence` an old-style model might still emit: the outcome is always the same."""
    extra = {} if legacy is None else {"confidence": legacy}
    bot = await run_with(
        tmp_path, *vision_and_whisper(),
        structured_response(make_analysis(), **extra), structured_response(make_report()),
    )

    assert len(bot.sent_voices) == 1 and len(bot.sent_documents) == 1  # the whole result was delivered
    assert not set(AI_FAILURE_MESSAGES.values()) & set(bot.sent_texts) and ERROR_MSG not in bot.sent_texts


async def test_malformed_model_output_is_not_reported_as_low_document_quality(tmp_path):
    """
    The analysis model replies with prose instead of the structure. That is a model/output-format failure, not a blurry
    scan: the user gets the format-failure message and nothing that blames the file.
    """
    bot = await run_with(tmp_path, *vision_and_whisper(), chat_response("Sorry, I cannot answer in JSON."))

    assert AI_FAILURE_MESSAGES[AIFailure.INVALID_OUTPUT] in bot.sent_texts
    assert not any("качеств" in text.lower() for text in bot.sent_texts)
    assert bot.sent_voices == [] and bot.sent_documents == []
