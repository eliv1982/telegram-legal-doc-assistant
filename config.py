"""
Конфигурация приложения из переменных окружения.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def getenv(key: str, default: str = "") -> str:
    return os.getenv(key, default)


BOT_TOKEN = getenv("BOT_TOKEN")
OPENAI_API_KEY = getenv("OPENAI_API_KEY")
SESSION_TIMEOUT_MINUTES = int(getenv("SESSION_TIMEOUT_MINUTES", "10"))

# Клиент OpenAI: один на процесс (см. services/openai_client.py). Тайм-аут — на один HTTP-запрос;
# повторы (429, 5xx, сетевые сбои) делает сам SDK с задержкой между попытками.
OPENAI_TIMEOUT_SECONDS = float(getenv("OPENAI_TIMEOUT_SECONDS", "90"))
OPENAI_MAX_RETRIES = int(getenv("OPENAI_MAX_RETRIES", "2"))

# Модели и голос. Значения по умолчанию можно переопределить в .env.
OPENAI_TRANSCRIPTION_MODEL = getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
OPENAI_TRANSCRIPTION_LANGUAGE = getenv("OPENAI_TRANSCRIPTION_LANGUAGE", "ru")
OPENAI_VISION_MODEL = getenv("OPENAI_VISION_MODEL", "gpt-4o")  # распознавание страниц-сканов и фото
OPENAI_ANALYSIS_MODEL = getenv("OPENAI_ANALYSIS_MODEL", "gpt-4o")
OPENAI_REPORT_MODEL = getenv("OPENAI_REPORT_MODEL", "gpt-4o-mini")  # достаточно для пост-обработки
OPENAI_TTS_MODEL = getenv("OPENAI_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE = getenv("OPENAI_TTS_VOICE", "alloy")
