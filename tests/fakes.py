"""
Offline stand-ins for Telegram and OpenAI used by the handler/pipeline tests.
Nothing here opens a socket.
"""
import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx2  # the HTTP layer of the pinned openai SDK: its MockTransport lets the real SDK run without a socket
from aiogram.fsm.storage.base import BaseEventIsolation, StorageKey
from aiogram.types import Chat, Document, Message, Update, User, Voice
from openai import AsyncOpenAI
from pydantic import BaseModel

from services.schemas import AnalysisResult, ChecklistItem, Issue, ReportResult
from tests import samples

# What FakeBot.download_file serves by destination suffix; a voice file (.ogg) stays an opaque placeholder.
SAMPLE_FILES: dict[str, Callable[[], bytes]] = {
    ".png": samples.png_bytes,
    ".jpg": samples.jpeg_bytes,
    ".jpeg": samples.jpeg_bytes,
    ".webp": samples.webp_bytes,
    ".pdf": samples.text_pdf,
}

# Shaped like a real bot token (<id>:<35 chars>) but made up; used to prove tokens never reach the logs.
FAKE_BOT_TOKEN = "123456789:AAFakeTokenForTests-abcdefghijklmnop_QRS"


class FakeStatusMessage:
    """What `bot.send_message` returns: the pipeline later edits/deletes it."""

    async def delete(self) -> None:
        pass

    async def edit_text(self, text: str, **kwargs) -> None:
        pass


class FakeBot:
    """
    Records what the pipeline would have sent. `download_file` writes a file the validation layer accepts for
    the destination's type (a real tiny PNG/JPEG/WEBP/PDF), or exactly the bytes a test registered in `payloads`.
    """

    id = 42  # what aiogram's FSM middleware reads from a real Bot

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.sent_kwargs: list[dict] = []  # the keyword arguments of each send_message, parallel to sent_texts
        self.sent_voices: list[object] = []
        self.sent_documents: list[object] = []
        self.document_captions: list[str | None] = []
        # Lets a test hold a download open (see test_fsm_regressions.py) without any sleeps.
        self.before_download: Callable[[str], Awaitable[None]] | None = None
        # remote path ("remote/<file_id>") -> bytes to serve instead of the default sample for the file type
        self.payloads: dict[str, bytes] = {}

    async def __call__(self, method, request_timeout=None) -> FakeStatusMessage:
        """Bound shortcuts such as `Message.answer` (only used when updates go through a Dispatcher)."""
        self.sent_texts.append(method.text)
        return FakeStatusMessage()

    async def get_file(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(file_path=f"remote/{file_id}")

    async def download_file(self, file_path: str, destination: str | Path) -> None:
        if self.before_download is not None:
            await self.before_download(file_path)
        destination = Path(destination)
        default = SAMPLE_FILES.get(destination.suffix.lower(), lambda: b"placeholder:" + file_path.encode())()
        destination.write_bytes(self.payloads.get(file_path, default))

    async def send_message(self, chat_id: int, text: str, **kwargs) -> FakeStatusMessage:
        self.sent_texts.append(text)
        self.sent_kwargs.append(kwargs)
        return FakeStatusMessage()

    async def send_voice(self, chat_id: int, voice: object, **kwargs) -> None:
        self.sent_voices.append(voice)

    async def send_document(self, chat_id: int, document: object, **kwargs) -> None:
        self.sent_documents.append(document)
        self.document_captions.append(kwargs.get("caption"))


class FakeMessage:
    """The few `aiogram.types.Message` attributes the handlers touch."""

    def __init__(self, user_id: int = 1, voice=None, document=None) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.voice = voice
        self.document = document
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)

    @classmethod
    def with_voice(cls, unique_id: str = "v1", user_id: int = 1, file_size: int | None = None) -> "FakeMessage":
        voice = SimpleNamespace(file_id=f"voice-{unique_id}", file_unique_id=unique_id, file_size=file_size)
        return cls(user_id=user_id, voice=voice)

    @classmethod
    def with_document(
        cls, file_name: str | None = "contract.png", unique_id: str = "d1", user_id: int = 1,
        file_size: int | None = None, mime_type: str | None = None,
    ) -> "FakeMessage":
        document = SimpleNamespace(
            file_name=file_name, file_id=f"doc-{unique_id}", file_unique_id=unique_id,
            file_size=file_size, mime_type=mime_type,
        )
        return cls(user_id=user_id, document=document)


class FakeOpenAIService:
    """
    Stands in for services.openai_service.OpenAIService when the pipeline itself is under test.
    `calls` lists every (paid) method that was reached, in order; an empty list proves nothing was paid for.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.ocr_images: list[bytes] = []  # what Vision was asked to read
        self.analysis_inputs: list[str] = []  # the document text the analysis model was given
        self.report_inputs: list[AnalysisResult] = []  # what the report model was given

    async def transcribe_voice(self, audio_path) -> str:
        self.calls.append("transcribe_voice")
        return "Проверь договор на риски"

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        self.calls.append("extract_text_from_image")
        self.ocr_images.append(image_bytes)
        return "Текст договора"

    async def analyze_document(self, voice_transcript: str, document_text: str) -> AnalysisResult:
        self.calls.append("analyze_document")
        self.analysis_inputs.append(document_text)
        return make_analysis()

    async def generate_report(self, *, task: str, analysis: AnalysisResult) -> ReportResult:
        self.calls.append("generate_report")
        self.report_inputs.append(analysis)
        return make_report()


def make_analysis(*issues: Issue) -> AnalysisResult:
    """No findings unless given: 'the model found nothing' is a valid result, and it keeps the report text exact."""
    return AnalysisResult(
        document_type="Договор", summary="Краткая суть договора.", key_facts=[], issues=list(issues)
    )


def make_issue(title: str = "Нечёткая формулировка", evidence: str | None = None, priority="low") -> Issue:
    return Issue(priority=priority, title=title, description="Описание замечания.", evidence=evidence)


def make_report(text_report: str = "Отчёт", tts_script: str = "Резюме", *items: ChecklistItem) -> ReportResult:
    return ReportResult(
        text_report=text_report,
        tts_script=tts_script,
        checklist_items=list(items) or [ChecklistItem(priority="high", text="Проверить контрагента")],
    )


class FakeTTSService:
    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []  # what was asked to be spoken

    async def text_to_speech(self, text: str) -> bytes:
        self.calls += 1
        self.texts.append(text)
        return b"ID3placeholder"


def chat_response(content: str | None = None, *, refusal: str | None = None, finish_reason: str = "stop") -> httpx2.Response:
    """A well-formed /chat/completions reply, as the real API would send it."""
    message = {"role": "assistant", "content": content, "refusal": refusal}
    return httpx2.Response(200, json={
        "id": "chatcmpl-offline", "object": "chat.completion", "created": 1, "model": "offline",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
    })


def structured_response(result: BaseModel, **extra) -> httpx2.Response:
    """A structured-output reply: the model's JSON, optionally with extra (unknown) fields mixed in."""
    return chat_response(json.dumps({**result.model_dump(), **extra}, ensure_ascii=False))


def speech_response(audio: bytes = b"ID3placeholder") -> httpx2.Response:
    return httpx2.Response(200, content=audio, headers={"content-type": "audio/mpeg"})


def transcription_response(text: str = "Проверь договор на риски") -> httpx2.Response:
    return httpx2.Response(200, text=text, headers={"content-type": "text/plain"})


def error_response(status: int, code: str | None = None) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"message": "SENTINEL-PROVIDER-TEXT", "type": "x", "code": code}})


class ScriptedOpenAI:
    """
    The REAL `AsyncOpenAI` of the pinned SDK (real request building, response parsing and exception classes) on a
    scripted transport: replies are served in order, and no socket is ever opened. `max_retries=0` keeps error replies instant.
    """

    def __init__(self, *replies: httpx2.Response | Exception) -> None:
        self.requests: list[httpx2.Request] = []
        self._replies = list(replies)
        self.client = AsyncOpenAI(
            api_key="offline-test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle)),
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError(f"unscripted OpenAI request: {request.url.path}")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def json_bodies(self) -> list[dict]:
        """The JSON bodies the SDK actually put on the wire (multipart uploads are skipped)."""
        return [json.loads(r.content) for r in self.requests if r.headers["content-type"].startswith("application/json")]


class Gate:
    """Deterministic hand-off between two coroutines: `reached` is set, then the holder waits for `release`."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def hold(self) -> None:
        self.reached.set()
        await self.release.wait()


class ObservedIsolation(BaseEventIsolation):
    """
    Wraps the isolation the app configured and queues every lock request. A test can therefore wait until an
    update has reached the isolation boundary (running on, or parked behind a running update) without sleeping.
    """

    def __init__(self, inner: BaseEventIsolation) -> None:
        self.inner = inner
        self.requests: asyncio.Queue[StorageKey] = asyncio.Queue()

    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        self.requests.put_nowait(key)
        async with self.inner.lock(key):
            yield

    async def close(self) -> None:
        await self.inner.close()


def _update(update_id: int, user_id: int, **content) -> Update:
    message = Message(
        message_id=update_id,
        date=datetime(2026, 1, 1),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        **content,
    )
    return Update(update_id=update_id, message=message)


def voice_update(update_id: int, user_id: int, unique_id: str = "v1") -> Update:
    """A real aiogram Update, for tests that go through the Dispatcher."""
    return _update(update_id, user_id, voice=Voice(file_id=f"voice-{unique_id}", file_unique_id=unique_id, duration=1))


def document_update(update_id: int, user_id: int, unique_id: str = "d1", file_name: str = "contract.png") -> Update:
    document = Document(file_id=f"doc-{unique_id}", file_unique_id=unique_id, file_name=file_name)
    return _update(update_id, user_id, document=document)
