"""
Offline stand-ins for Telegram and OpenAI used by the handler/pipeline tests.
Nothing here opens a socket.
"""
import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from aiogram.fsm.storage.base import BaseEventIsolation, StorageKey
from aiogram.types import Chat, Document, Message, Update, User, Voice

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
        self.sent_voices: list[object] = []
        self.sent_documents: list[object] = []
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
        return FakeStatusMessage()

    async def send_voice(self, chat_id: int, voice: object, **kwargs) -> None:
        self.sent_voices.append(voice)

    async def send_document(self, chat_id: int, document: object, **kwargs) -> None:
        self.sent_documents.append(document)


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

    async def transcribe_voice(self, audio_path) -> str:
        self.calls.append("transcribe_voice")
        return "Проверь договор на риски"

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        self.calls.append("extract_text_from_image")
        self.ocr_images.append(image_bytes)
        return "Текст договора"

    async def analyze_document(self, voice_transcript: str, document_text: str) -> dict:
        self.calls.append("analyze_document")
        self.analysis_inputs.append(document_text)
        return {
            "document_type": "Договор",
            "confidence": 92,
            "user_task": "Анализ рисков",
            "issues_found": [{"description": "Нечёткая формулировка", "priority": "Низкий"}],
        }

    async def generate_response_sections(self, **kwargs) -> dict[str, str]:
        self.calls.append("generate_response_sections")
        return {"text_report": "Отчёт", "tts_script": "Резюме", "checklist": "□ Проверить контрагента"}


class FakeTTSService:
    def __init__(self) -> None:
        self.calls = 0

    async def text_to_speech(self, text: str) -> bytes:
        self.calls += 1
        return b"ID3placeholder"


class FakeOpenAIClient:
    """
    Stands in for `AsyncOpenAI` so the *real* OpenAIService can run offline.
    `chat_replies` are returned in order by successive chat.completions.create calls.
    """

    def __init__(self, chat_replies: list[str], transcript: str = "Проверь договор на риски") -> None:
        self._chat_replies = list(chat_replies)
        self._transcript = transcript
        self.chat_calls = 0
        self.transcribe_calls = 0
        self.speech_calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat_create))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self._transcribe),
            speech=SimpleNamespace(create=self._speech),
        )

    async def _chat_create(self, **kwargs) -> SimpleNamespace:
        self.chat_calls += 1
        content = self._chat_replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    async def _transcribe(self, **kwargs) -> str:
        self.transcribe_calls += 1
        return self._transcript

    async def _speech(self, **kwargs) -> SimpleNamespace:
        self.speech_calls.append(kwargs)
        return SimpleNamespace(content=b"ID3placeholder")


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
