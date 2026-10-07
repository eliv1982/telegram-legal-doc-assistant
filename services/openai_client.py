"""
Один клиент OpenAI на процесс: создаётся при запуске бота, передаётся сервисам и закрывается при остановке.
Тайм-аут и число повторов заданы явно, а не берутся из значений SDK по умолчанию (10 минут и 2 повтора).
"""
from dataclasses import dataclass

from openai import AsyncOpenAI, Timeout

import config
from services.openai_service import OpenAIService
from services.tts_service import TTSService

CONNECT_TIMEOUT_SECONDS = 10.0


def create_openai_client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        timeout=Timeout(config.OPENAI_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        max_retries=config.OPENAI_MAX_RETRIES,
    )


@dataclass
class AIServices:
    """Сервисы на общем клиенте. Хендлеры получают их через workflow_data aiogram."""

    openai: OpenAIService
    tts: TTSService

    @classmethod
    def around(cls, client: AsyncOpenAI) -> "AIServices":
        return cls(openai=OpenAIService(client), tts=TTSService(client))
