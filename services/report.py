"""
Сборка сообщений для пользователя. Всё, что нельзя доверять модели, добавляется здесь, кодом, и после сокращения
текста модели, поэтому не может потеряться:

  заголовок «оценка модели»  ->  текст модели (при необходимости сокращённый)  ->  цитаты из документа,
  найденные в тексте дословно  ->  строка охвата (детерминированные данные извлечения)  ->  дисклеймер.

Три вида содержимого различимы: охват — факт об извлечении; текст модели — её оценка; цитата показывается как
цитата только после проверки по тексту документа (services.grounding).
"""
from collections.abc import Sequence

from services.grounding import GroundedIssue
from services.schemas import PRIORITY_LABELS

REPORT_HEADER = "🤖 Предварительный разбор: оценка модели ИИ"
DISCLAIMER = (
    "⚠️ Это автоматический предварительный разбор (first-pass review), а не юридическая консультация. "
    "Оценку выполнила модель ИИ: она может ошибаться и не заменяет проверку юристом."
)
TTS_DISCLAIMER = "Это предварительная оценка модели, а не юридическая консультация."

QUOTES_HEADER = "📎 Цитаты из документа (найдены в его тексте дословно):"
# Блок цитат ограничен, чтобы отчёту модели всегда оставалось место.
MAX_QUOTES_CHARS = 1200
ELLIPSIS = "…"

_SEPARATOR = "\n\n"
_MARKDOWN_SPECIALS = "_*`["


def escape_markdown(text: str) -> str:
    """Экранирование для Markdown Telegram (режим по умолчанию бота): цитата не должна ломать разметку сообщения."""
    return "".join(f"\\{ch}" if ch in _MARKDOWN_SPECIALS else ch for ch in text)


def _quotes_block(findings: Sequence[GroundedIssue]) -> str:
    quoted = [finding for finding in findings if finding.verified_evidence]
    lines: list[str] = []
    if quoted:
        lines.append(QUOTES_HEADER)
        used = len(QUOTES_HEADER)
        for index, finding in enumerate(quoted):
            label = PRIORITY_LABELS[finding.issue.priority]
            line = f"• [{label}] {escape_markdown(finding.issue.title)}: «{escape_markdown(finding.verified_evidence)}»"
            if used + len(line) + 1 > MAX_QUOTES_CHARS:
                lines.append(f"…и ещё цитат: {len(quoted) - index}")
                break
            lines.append(line)
            used += len(line) + 1
    unconfirmed = len(findings) - len(quoted)
    if unconfirmed:
        lines.append(
            f"Для {unconfirmed} из {len(findings)} замечаний нет подтверждающей цитаты из текста документа: "
            "это оценка модели."
        )
    return "\n".join(lines)


def compose_report(model_text: str, findings: Sequence[GroundedIssue], coverage: str, *, limit: int) -> str:
    """
    Сообщение с отчётом не длиннее `limit`. Сокращается только текст модели: заголовок, цитаты, охват и дисклеймер
    добавляются целиком (их размер ограничен, см. MAX_QUOTES_CHARS).
    """
    quotes = _quotes_block(findings)
    footer = f"{coverage}{_SEPARATOR}{DISCLAIMER}"
    fixed = [REPORT_HEADER, *([quotes] if quotes else []), footer]
    budget = limit - sum(len(part) for part in fixed) - len(_SEPARATOR) * len(fixed)
    body = model_text.strip()
    if len(body) > budget:
        body = body[: max(budget - len(ELLIPSIS), 0)].rstrip() + ELLIPSIS
    return _SEPARATOR.join([REPORT_HEADER, body, *([quotes] if quotes else []), footer])


def compose_tts_script(model_script: str, *, limit: int) -> str:
    """Текст для озвучки; пометка о предварительной оценке добавляется после сокращения и не теряется."""
    suffix = f" {TTS_DISCLAIMER}"
    return model_script.strip()[: limit - len(suffix)] + suffix
