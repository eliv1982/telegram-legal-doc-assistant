"""
Offline stand-ins for Telegram and OpenAI used by the handler/pipeline tests.
Nothing here opens a socket.
"""
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace


class FakeStatusMessage:
    """What `bot.send_message` returns: the pipeline later edits/deletes it."""

    async def delete(self) -> None:
        pass

    async def edit_text(self, text: str, **kwargs) -> None:
        pass


class FakeBot:
    """Records what the pipeline would have sent; `download_file` just writes a placeholder file."""

    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.sent_voices: list[object] = []
        self.sent_documents: list[object] = []
        # Lets a test hold a download open (see test_fsm_regressions.py) without any sleeps.
        self.before_download: Callable[[str], Awaitable[None]] | None = None

    async def get_file(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(file_path=f"remote/{file_id}")

    async def download_file(self, file_path: str, destination: str | Path) -> None:
        if self.before_download is not None:
            await self.before_download(file_path)
        Path(destination).write_bytes(b"placeholder:" + file_path.encode())

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
    def with_voice(cls, unique_id: str = "v1", user_id: int = 1) -> "FakeMessage":
        voice = SimpleNamespace(file_id=f"voice-{unique_id}", file_unique_id=unique_id)
        return cls(user_id=user_id, voice=voice)

    @classmethod
    def with_document(cls, file_name: str = "contract.png", unique_id: str = "d1", user_id: int = 1) -> "FakeMessage":
        document = SimpleNamespace(file_name=file_name, file_id=f"doc-{unique_id}", file_unique_id=unique_id)
        return cls(user_id=user_id, document=document)


class FakeOpenAIService:
    """Stands in for services.openai_service.OpenAIService when the pipeline itself is under test."""

    async def transcribe_voice(self, audio_path) -> str:
        return "Проверь договор на риски"

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return "Текст договора"

    async def analyze_document(self, voice_transcript: str, document_text: str) -> dict:
        return {
            "document_type": "Договор",
            "confidence": 92,
            "user_task": "Анализ рисков",
            "issues_found": [{"description": "Нечёткая формулировка", "priority": "Низкий"}],
        }

    async def generate_response_sections(self, **kwargs) -> dict[str, str]:
        return {"text_report": "Отчёт", "tts_script": "Резюме", "checklist": "□ Проверить контрагента"}


class FakeTTSService:
    async def text_to_speech(self, text: str, lang: str = "ru") -> bytes:
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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat_create))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._transcribe))

    async def _chat_create(self, **kwargs) -> SimpleNamespace:
        self.chat_calls += 1
        content = self._chat_replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    async def _transcribe(self, **kwargs) -> str:
        return self._transcript


class Gate:
    """Deterministic hand-off between two coroutines: `reached` is set, then the holder waits for `release`."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def hold(self) -> None:
        self.reached.set()
        await self.release.wait()
