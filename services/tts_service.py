"""
Генерация голосового сообщения из текста через OpenAI TTS.
"""
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(self, openai_api_key: str | None = None):
        self._openai = AsyncOpenAI(api_key=openai_api_key)

    async def text_to_speech(self, text: str) -> bytes:
        """
        Преобразует текст в аудио.
        :return: байты MP3
        """
        return await self._openai_tts(text)

    async def _openai_tts(self, text: str) -> bytes:
        """OpenAI TTS (модель tts-1 или tts-1-hd)."""
        response = await self._openai.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text[:4096],
        )
        return response.content
