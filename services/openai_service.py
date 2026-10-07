"""
Адаптер OpenAI Chat Completions: Whisper, Vision (распознавание страниц-сканов), анализ и отчёт.

Анализ и отчёт идут через `chat.completions.parse` с моделью pydantic: SDK передаёт JSON Schema (strict) и
возвращает уже проверенный объект. Разбора свободного текста и «спасения» JSON регулярками нет:
отказ модели, обрыв по лимиту токенов и несоответствие схеме — это исходы сервиса (services.ai_errors),
а не свойство документа.
"""
import base64
import logging
from pathlib import Path
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

import config
from prompts.analysis_prompt import DOCUMENT_OCR_PROMPT, build_analysis_messages
from prompts.response_prompt import build_report_messages
from services import limits
from services.ai_errors import AIFailure, AIServiceError, translate_openai_errors
from services.schemas import AnalysisResult, ReportResult

logger = logging.getLogger(__name__)

# Лимит токенов ответа: параметр max_completion_tokens (прежний параметр объявлен в API устаревшим).
ANALYSIS_MAX_COMPLETION_TOKENS = 4096
REPORT_MAX_COMPLETION_TOKENS = 4096
# Страница всё равно сокращается до limits.MAX_PAGE_CHARS после распознавания и это указывается в охвате,
# поэтому обрыв распознавания по лимиту токенов не прячет засчитанный текст и ошибкой не считается.
OCR_MAX_COMPLETION_TOKENS = 4096

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAIService:
    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def transcribe_voice(self, audio_path: str | Path) -> str:
        """Транскрибация голосового сообщения (Whisper)."""
        with translate_openai_errors(), open(audio_path, "rb") as f:
            response = await self._client.audio.transcriptions.create(
                model=config.OPENAI_TRANSCRIPTION_MODEL,
                file=f,
                response_format="text",
                language=config.OPENAI_TRANSCRIPTION_LANGUAGE,
            )
        return response if isinstance(response, str) else str(response)

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """Извлечение текста из изображения документа (Vision)."""
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        with translate_openai_errors():
            response = await self._client.chat.completions.create(
                model=config.OPENAI_VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": DOCUMENT_OCR_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                        ],
                    }
                ],
                max_completion_tokens=OCR_MAX_COMPLETION_TOKENS,
            )
        choice = _first_choice(response)
        # Отказ Vision — исход сервиса, а не «в документе нет текста» (иначе пользователю соврут про файл).
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise AIServiceError(AIFailure.REFUSED, "vision")
        return (choice.message.content or "").strip()

    async def analyze_document(self, voice_transcript: str, document_text: str) -> AnalysisResult:
        """Анализ документа по задаче пользователя. Возвращает проверенную схемой структуру."""
        messages = build_analysis_messages(
            voice_transcript,
            # Текст, прошедший services.document_extraction, в лимит гарантированно умещается
            # (см. limits.ANALYSIS_CHAR_LIMIT), так что срез никогда не отрезает засчитанные страницы.
            document_text[: limits.ANALYSIS_CHAR_LIMIT],
        )
        return await self._parse(
            AnalysisResult,
            model=config.OPENAI_ANALYSIS_MODEL,
            messages=messages,
            max_completion_tokens=ANALYSIS_MAX_COMPLETION_TOKENS,
        )

    async def generate_report(self, *, task: str, analysis: AnalysisResult) -> ReportResult:
        """Отчёт, текст голосового резюме и пункты чек-листа по результату анализа."""
        return await self._parse(
            ReportResult,
            model=config.OPENAI_REPORT_MODEL,
            messages=build_report_messages(task, analysis),
            max_completion_tokens=REPORT_MAX_COMPLETION_TOKENS,
        )

    async def _parse(
        self, schema: type[SchemaT], *, model: str, messages: list[dict[str, str]], max_completion_tokens: int
    ) -> SchemaT:
        # SDK сам бросает LengthFinishReasonError (обрыв по токенам) и ContentFilterFinishReasonError, а при ответе,
        # не соответствующем схеме, — ValidationError; translate_openai_errors превращает их в AIServiceError.
        with translate_openai_errors():
            completion = await self._client.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=schema,
                max_completion_tokens=max_completion_tokens,
            )
        message = _first_choice(completion).message
        if message.refusal:
            raise AIServiceError(AIFailure.REFUSED, schema.__name__)  # текст отказа не сохраняем: он может цитировать документ
        if message.parsed is None:
            raise AIServiceError(AIFailure.INVALID_OUTPUT, f"{schema.__name__}: empty")
        return message.parsed


def _first_choice(completion):
    if not completion.choices:
        raise AIServiceError(AIFailure.INVALID_OUTPUT, "no choices")
    return completion.choices[0]
