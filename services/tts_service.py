"""
Генерация голосового сообщения из текста через OpenAI TTS (клиент общий с OpenAIService).
"""
import logging

from openai import AsyncOpenAI

import config
from services.ai_errors import translate_openai_errors

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def text_to_speech(self, text: str) -> bytes:
        """
        Преобразует текст в аудио как есть: бюджет текста и предел входа API (limits.TTS_INPUT_LIMIT) соблюдает
        services.report.compose_tts_script по границам предложений, а не срезом по знакам.
        :return: байты MP3
        """
        with translate_openai_errors():
            response = await self._client.audio.speech.create(
                model=config.OPENAI_TTS_MODEL,
                voice=config.OPENAI_TTS_VOICE,
                input=text,
            )
        return response.content
