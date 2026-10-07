"""
Исходы вызова модели/сервиса OpenAI, которые отличаются для пользователя. Их шесть, не больше: от сбоя
формата ответа нельзя «лечить» повторной отправкой того же документа, а от лимита запросов — можно.

Ни одно значение здесь не несёт текст ответа провайдера или модели: он может содержать фрагменты документа.
Для журнала хранится только класс исходной ошибки (и код ответа, если он был).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum

import openai
from pydantic import ValidationError


class AIFailure(Enum):
    CONFIG = "config"  # ключ/доступ/имя модели/квота: чинит администратор, а не пользователь
    UNAVAILABLE = "unavailable"  # лимит запросов, сбой провайдера, нет соединения (после повторов SDK)
    TIMEOUT = "timeout"
    REFUSED = "refused"  # модель отказалась отвечать или ответ заблокирован фильтром содержимого
    TRUNCATED = "truncated"  # ответ оборвался по лимиту токенов: структура неполна
    INVALID_OUTPUT = "invalid_output"  # ответ не соответствует схеме


class AIServiceError(Exception):
    """Исход вызова ИИ. `kind` определяет сообщение пользователю; текста провайдера здесь нет."""

    def __init__(self, kind: AIFailure, cause: str = "") -> None:
        super().__init__(kind.value, cause)
        self.kind = kind
        self.cause = cause

    def __str__(self) -> str:
        return f"{self.kind.value} ({self.cause})" if self.cause else self.kind.value


def classify(exc: BaseException) -> AIServiceError | None:
    """Известная ошибка SDK/схемы -> AIServiceError; всё остальное (чужие исключения) -> None."""
    cause = type(exc).__qualname__
    if isinstance(exc, openai.APITimeoutError):  # раньше APIConnectionError: это его подкласс
        return AIServiceError(AIFailure.TIMEOUT, cause)
    if isinstance(exc, openai.LengthFinishReasonError):
        return AIServiceError(AIFailure.TRUNCATED, cause)
    if isinstance(exc, openai.ContentFilterFinishReasonError):
        return AIServiceError(AIFailure.REFUSED, cause)
    if isinstance(exc, ValidationError):
        return AIServiceError(AIFailure.INVALID_OUTPUT, cause)  # текст ошибки pydantic содержит ответ модели
    if isinstance(exc, openai.RateLimitError):
        # Исчерпанная квота — вопрос оплаты, повтор не поможет; обычный лимит скорости — временный.
        kind = AIFailure.CONFIG if exc.code == "insufficient_quota" else AIFailure.UNAVAILABLE
        return AIServiceError(kind, f"{cause}({exc.status_code})")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)):
        return AIServiceError(AIFailure.CONFIG, f"{cause}({exc.status_code})")
    if isinstance(exc, (openai.InternalServerError, openai.APIConnectionError)):
        return AIServiceError(AIFailure.UNAVAILABLE, cause)
    return None


@contextmanager
def translate_openai_errors() -> Iterator[None]:
    """Оборачивает один вызов SDK. Неизвестные исключения проходят как есть и дойдут до общего обработчика."""
    try:
        yield
    except Exception as exc:
        failure = classify(exc)
        if failure is None:
            raise
        raise failure from None  # без цепочки: в ней текст ответа провайдера или модели
