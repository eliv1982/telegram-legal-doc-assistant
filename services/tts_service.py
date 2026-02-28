"""
Генерация голосового сообщения из текста (gTTS или OpenAI TTS).
"""
import io
import logging
import tempfile
from pathlib import Path

from gtts import gTTS
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(self, provider: str = "gtts", openai_api_key: str | None = None):
        self.provider = provider.lower()
        self._openai = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None

    async def text_to_speech(self, text: str, lang: str = "ru") -> bytes:
        """
        Преобразует текст в аудио.
        :return: байты MP3
        """
        if self.provider == "openai" and self._openai:
            return await self._openai_tts(text)
        return await self._gtts_tts(text, lang)

    async def _gtts_tts(self, text: str, lang: str) -> bytes:
        """gTTS — синхронный вызов в executor."""
        import asyncio

        def _generate():
            tts = gTTS(text=text[:500], lang=lang, slow=False)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            return buf.read()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _generate)

    async def _openai_tts(self, text: str) -> bytes:
        """OpenAI TTS (модель tts-1 или tts-1-hd)."""
        response = await self._openai.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text[:4096],
        )
        return response.content
