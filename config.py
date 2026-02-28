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
TTS_PROVIDER = getenv("TTS_PROVIDER", "gtts").lower()
SESSION_TIMEOUT_MINUTES = int(getenv("SESSION_TIMEOUT_MINUTES", "10"))
CHECKLIST_FORMAT = getenv("CHECKLIST_FORMAT", "pdf").lower()
if CHECKLIST_FORMAT not in ("pdf", "png"):
    CHECKLIST_FORMAT = "pdf"
