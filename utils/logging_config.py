"""
Настройка логирования для бота.
"""
import logging
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"
# Токен бота: <bot_id>:<секрет>, в том числе внутри URL вида .../bot<токен>/getFile.
_BOT_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
_OPENAI_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")


def redact(text: str) -> str:
    return _OPENAI_KEY_RE.sub(REDACTED, _BOT_TOKEN_RE.sub(REDACTED, text))


class RedactSecretsFilter(logging.Filter):
    """
    Эшелон защиты: маскирует токены бота и ключи OpenAI во всём, что доходит до обработчика
    (сообщение, аргументы, traceback). Не заменяет правильную обработку исключений:
    основная защита — не логировать текст исключений (см. log_failure).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:
            return True
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def log_failure(logger: logging.Logger, stage: str, exc: BaseException) -> None:
    """
    Логирует этап и класс исключения, но не его текст и не traceback: они могут содержать
    URL с токеном бота или фрагменты пользовательского документа.
    """
    logger.error("%s failed: %s.%s", stage, type(exc).__module__, type(exc).__qualname__)


def setup_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    """Настраивает логгеры."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.addFilter(RedactSecretsFilter())

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
