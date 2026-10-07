"""
The OpenAI adapter on the REAL pinned SDK with a scripted transport: what goes on the wire, what comes back
typed, and how every failure becomes one of a few service outcomes instead of a verdict on the document. No socket is opened.
"""
import json

import httpx2
import openai
import pytest

import bot as bot_main
import config
from handlers.document import AI_FAILURE_MESSAGES, ERROR_MSG, REJECTION_MESSAGES, run_pipeline
from services.ai_errors import AIFailure, AIServiceError
from services.openai_client import AIServices, create_openai_client
from services.openai_service import ANALYSIS_MAX_COMPLETION_TOKENS, REPORT_MAX_COMPLETION_TOKENS, OpenAIService
from services.schemas import AnalysisResult, ChecklistItem, ReportResult
from services.tts_service import TTSService
from tests import samples
from tests.fakes import (
    FakeBot,
    FakeOpenAIService,
    FakeTTSService,
    ScriptedOpenAI,
    chat_response,
    error_response,
    make_analysis,
    make_issue,
    make_report,
    speech_response,
    structured_response,
    transcription_response,
)


def analysis_json(**issue_changes) -> str:
    data = make_analysis(make_issue()).model_dump()
    data["issues"][0].update(issue_changes)
    return json.dumps(data, ensure_ascii=False)


# --- the request and the typed result ----------------------------------------------------------------------------------


async def test_analysis_is_requested_as_strict_structured_output_with_the_current_token_parameter():
    openai_ = ScriptedOpenAI(structured_response(make_analysis(make_issue("Заголовок"))))

    result = await OpenAIService(openai_.client).analyze_document("Проверь договор", "Текст договора")

    assert isinstance(result, AnalysisResult) and result.issues[0].title == "Заголовок"
    assert openai_.paths == ["/v1/chat/completions"]  # the Chat Completions family, no Responses API
    body = openai_.json_bodies()[0]
    assert body["model"] == "gpt-4o"
    assert body["max_completion_tokens"] == ANALYSIS_MAX_COMPLETION_TOKENS and "max_tokens" not in body
    schema = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema" and schema["strict"] is True and schema["name"] == "AnalysisResult"
    assert schema["schema"]["$defs"]["Issue"]["properties"]["priority"]["enum"] == ["high", "medium", "low"]
    assert "confidence" not in json.dumps(schema)  # the model is not asked to rate itself
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


async def test_the_report_arrives_as_typed_fields_with_structured_checklist_items():
    reply = make_report(
        "Текст отчёта", "Резюме", ChecklistItem(priority="high", text="Проверить"), ChecklistItem(priority="low", text="Запросить")
    )
    openai_ = ScriptedOpenAI(structured_response(reply))

    report = await OpenAIService(openai_.client).generate_report(task="Проверь", analysis=make_analysis())

    assert isinstance(report, ReportResult) and report == reply
    assert [item.priority for item in report.checklist_items] == ["high", "low"]
    body = openai_.json_bodies()[0]
    assert body["model"] == "gpt-4o-mini" and body["max_completion_tokens"] == REPORT_MAX_COMPLETION_TOKENS
    assert body["response_format"]["json_schema"]["name"] == "ReportResult"


async def test_models_voice_and_language_come_from_configuration(monkeypatch, tmp_path):
    for name, value in {
        "OPENAI_VISION_MODEL": "vision-x", "OPENAI_ANALYSIS_MODEL": "analysis-x", "OPENAI_REPORT_MODEL": "report-x",
        "OPENAI_TRANSCRIPTION_MODEL": "whisper-x", "OPENAI_TRANSCRIPTION_LANGUAGE": "de",
        "OPENAI_TTS_MODEL": "tts-x", "OPENAI_TTS_VOICE": "voice-x",
    }.items():
        monkeypatch.setattr(config, name, value)
    openai_ = ScriptedOpenAI(
        chat_response("текст"), structured_response(make_analysis()), structured_response(make_report()),
        transcription_response(), speech_response(),
    )
    service = OpenAIService(openai_.client)
    voice = tmp_path / "voice.ogg"
    voice.write_bytes(b"OggS")

    await service.extract_text_from_image(b"\xff\xd8\xff")
    await service.analyze_document("задача", "документ")
    await service.generate_report(task="задача", analysis=make_analysis())
    await service.transcribe_voice(voice)
    await TTSService(openai_.client).text_to_speech("резюме")

    vision, analysis, report, speech = openai_.json_bodies()
    assert [vision["model"], analysis["model"], report["model"]] == ["vision-x", "analysis-x", "report-x"]
    assert vision["max_completion_tokens"] > 0 and "max_tokens" not in vision
    assert (speech["model"], speech["voice"]) == ("tts-x", "voice-x")
    whisper = openai_.requests[3].content  # multipart form
    assert b'name="model"\r\n\r\nwhisper-x' in whisper and b'name="language"\r\n\r\nde' in whisper


# --- every failure is its own service outcome --------------------------------------------------------------------------

MODEL_FAILURES = [
    pytest.param(chat_response(None, refusal="No."), AIFailure.REFUSED, id="refusal"),
    pytest.param(chat_response(analysis_json(), finish_reason="content_filter"), AIFailure.REFUSED, id="content-filter"),
    pytest.param(chat_response('{"document_type": "Договор", "summ', finish_reason="length"), AIFailure.TRUNCATED, id="truncated"),
    pytest.param(chat_response("Sorry, I cannot answer in JSON."), AIFailure.INVALID_OUTPUT, id="prose"),
    pytest.param(chat_response(json.dumps({"document_type": "Договор"})), AIFailure.INVALID_OUTPUT, id="missing-fields"),
    pytest.param(chat_response(analysis_json(priority="Критический")), AIFailure.INVALID_OUTPUT, id="priority-outside-enum"),
    pytest.param(chat_response(None), AIFailure.INVALID_OUTPUT, id="empty-content"),
]
PROVIDER_FAILURES = [
    pytest.param(error_response(401, "invalid_api_key"), AIFailure.CONFIG, id="bad-key"),
    pytest.param(error_response(429, "insufficient_quota"), AIFailure.CONFIG, id="quota"),
    pytest.param(error_response(429, "rate_limit_exceeded"), AIFailure.UNAVAILABLE, id="rate-limit"),
    pytest.param(error_response(500), AIFailure.UNAVAILABLE, id="provider-5xx"),
    pytest.param(httpx2.ConnectError("no route"), AIFailure.UNAVAILABLE, id="no-connection"),
    pytest.param(httpx2.ReadTimeout("slow"), AIFailure.TIMEOUT, id="timeout"),
]


@pytest.mark.parametrize("reply, failure", MODEL_FAILURES + PROVIDER_FAILURES)
async def test_every_failure_of_the_analysis_call_is_a_distinct_service_outcome(reply, failure):
    openai_ = ScriptedOpenAI(reply)

    with pytest.raises(AIServiceError) as raised:
        await OpenAIService(openai_.client).analyze_document("задача", "документ")

    assert raised.value.kind is failure
    assert "SENTINEL" not in str(raised.value)  # provider text never travels with the outcome


@pytest.mark.parametrize("reply, failure", [MODEL_FAILURES[0], MODEL_FAILURES[3]])
async def test_the_report_call_has_the_same_outcomes(reply, failure):
    openai_ = ScriptedOpenAI(reply)

    with pytest.raises(AIServiceError) as raised:
        await OpenAIService(openai_.client).generate_report(task="задача", analysis=make_analysis())

    assert raised.value.kind is failure


async def test_a_vision_refusal_is_a_service_outcome_not_an_empty_document():
    openai_ = ScriptedOpenAI(chat_response(None, refusal="I cannot read this."))

    with pytest.raises(AIServiceError) as raised:
        await OpenAIService(openai_.client).extract_text_from_image(b"\xff\xd8\xff")

    assert raised.value.kind is AIFailure.REFUSED  # not "no readable text in the document"


async def test_an_error_the_adapter_does_not_know_is_not_mislabelled():
    openai_ = ScriptedOpenAI(error_response(400))

    with pytest.raises(openai.BadRequestError):  # reaches the pipeline's generic handler (ERROR_MSG), not a wrong outcome
        await OpenAIService(openai_.client).analyze_document("задача", "документ")


async def test_each_service_outcome_reaches_the_user_as_its_own_message(tmp_path):
    voice_path, doc_path = tmp_path / "voice.ogg", tmp_path / "doc.png"
    voice_path.write_bytes(b"placeholder")
    doc_path.write_bytes(samples.png_bytes())
    seen = {}
    for kind in AIFailure:

        class Failing(FakeOpenAIService):
            async def analyze_document(self, voice_transcript, document_text, kind=kind):
                raise AIServiceError(kind, "SomeSdkError")

        bot = FakeBot()
        await run_pipeline(bot, 1, voice_path, doc_path, Failing(), FakeTTSService())
        seen[kind] = bot.sent_texts[-1]

    assert seen == AI_FAILURE_MESSAGES


def test_every_outcome_has_a_short_message_that_never_blames_the_document():
    assert set(AI_FAILURE_MESSAGES) == set(AIFailure)
    messages = list(AI_FAILURE_MESSAGES.values())
    assert len(set(messages)) == len(messages)
    for kind, text in AI_FAILURE_MESSAGES.items():
        assert len(text) < 200 and text != ERROR_MSG and text not in REJECTION_MESSAGES.values(), kind
        assert not any(word in text.lower() for word in ("качеств", "скан", "нечёт", "разрешени")), kind


# --- the one client: explicit timeout and retries, shared, closed at shutdown ------------------------------------------


async def test_the_client_has_an_explicit_timeout_and_retry_count_and_is_shared(monkeypatch):
    client = create_openai_client("offline-test-key")
    assert client.timeout == openai.Timeout(90.0, connect=10.0) and client.max_retries == 2  # not the SDK's 10 min default

    monkeypatch.setattr(config, "OPENAI_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(config, "OPENAI_MAX_RETRIES", 0)
    configured = create_openai_client("offline-test-key")
    assert configured.timeout.read == 30.0 and configured.max_retries == 0

    ai = AIServices.around(client)
    assert ai.openai._client is client and ai.tts._client is client  # one client for every OpenAI call
    await client.close()
    await configured.close()


@pytest.mark.parametrize("polling_fails", [False, True])
async def test_the_shared_client_is_closed_when_the_bot_stops(monkeypatch, polling_fails):
    closed, built = [], []

    class FakeClient:
        async def close(self):
            closed.append(True)

    class FakeDispatcher:
        def __init__(self, ai):
            built.append(ai)

        async def start_polling(self, *bots):
            if polling_fails:
                raise RuntimeError("polling died")

    client = FakeClient()
    monkeypatch.setattr(config, "BOT_TOKEN", "123456789:AAFakeTokenForTests-abcdefghijklmnop_QRS")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(bot_main, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(bot_main, "purge_stale_workspaces", lambda seconds: 0)
    monkeypatch.setattr(bot_main, "Bot", lambda **kwargs: object())
    monkeypatch.setattr(bot_main, "create_openai_client", lambda api_key: client)
    monkeypatch.setattr(bot_main, "build_dispatcher", FakeDispatcher)

    if polling_fails:
        with pytest.raises(RuntimeError):
            await bot_main.main()
    else:
        await bot_main.main()

    assert closed == [True]
    assert built[0].openai._client is client  # the dispatcher's services were built around the client that gets closed
