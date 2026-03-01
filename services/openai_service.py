"""
Сервис для работы с OpenAI API: Whisper, GPT-4 Vision, GPT-4.
"""
import base64
import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from prompts.analysis_prompt import ANALYSIS_PROMPT, DOCUMENT_OCR_PROMPT, RISK_SCALE
from prompts.response_prompt import RESPONSE_PROMPT, RISK_SCALE_RESPONSE
from utils.helpers import extract_json_from_text

logger = logging.getLogger(__name__)

# Модели
WHISPER_MODEL = "whisper-1"
VISION_MODEL = "gpt-4o"
ANALYSIS_MODEL = "gpt-4o"
RESPONSE_MODEL = "gpt-4o-mini"  # достаточно для пост-обработки


class OpenAIService:
    def __init__(self, api_key: str | None = None):
        self._client = AsyncOpenAI(api_key=api_key)

    async def transcribe_voice(self, audio_path: str | Path) -> str:
        """
        Транскрибация голосового сообщения через Whisper.
        """
        with open(audio_path, "rb") as f:
            response = await self._client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="text",
                language="ru",
            )
        return response if isinstance(response, str) else str(response)

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """
        Извлечение текста из изображения документа через GPT-4 Vision.
        """
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = await self._client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DOCUMENT_OCR_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        },
                    ],
                }
            ],
            max_tokens=4096,
        )
        text = response.choices[0].message.content or ""
        return text.strip()

    async def analyze_document(self, voice_transcript: str, document_text: str) -> dict[str, Any]:
        """
        Анализ документа по голосовой задаче и тексту документа.
        Возвращает структурированный JSON.
        """
        prompt = ANALYSIS_PROMPT.format(
            voice_transcript=voice_transcript,
            document_text=document_text[:15000],  # ограничение на токены
            risk_scale=RISK_SCALE,
        )
        response = await self._client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=[
                {"role": "system", "content": "Ты юридический ассистент. Отвечай строго в формате JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = extract_json_from_text(raw)
        if parsed is None:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Не удалось распарсить JSON анализа: %s", raw[:200])
                parsed = {
                    "document_type": "неизвестно",
                    "confidence": "0%",
                    "extracted_data": {},
                    "user_task": voice_transcript[:200],
                    "issues_found": [],
                    "requires_clarification": False,
                    "clarification_question": None,
                }
        return parsed

    async def generate_response_sections(
        self,
        analysis_json: dict[str, Any],
        document_type: str,
        user_task: str,
        issues_list: list[dict] | list[str],
    ) -> dict[str, str]:
        """
        Генерирует три секции: TEXT_REPORT, TTS_SCRIPT, CHECKLIST.
        """
        import json

        prompt = RESPONSE_PROMPT.format(
            analysis_json=json.dumps(analysis_json, ensure_ascii=False, indent=2),
            document_type=document_type,
            user_task=user_task,
            issues_list=json.dumps(issues_list, ensure_ascii=False),
            RISK_SCALE_RESPONSE=RISK_SCALE_RESPONSE,
        )
        response = await self._client.chat.completions.create(
            model=RESPONSE_MODEL,
            messages=[
                {"role": "system", "content": "Ты готовишь понятный отчёт для пользователя. Строго следуй формату с маркерами."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
        )
        raw = response.choices[0].message.content or ""
        from utils.helpers import parse_response_sections

        return parse_response_sections(raw)
