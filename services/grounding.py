"""
Проверка цитат модели по извлечённому тексту документа. Это НЕ проверка по законам или иным источникам:
проверяется лишь, что приведённая цитата действительно есть в проанализированном тексте.

Правило одно и детерминированное: после схлопывания пробельных символов цитата должна входить в текст
дословно (без регистра, без нечёткого сравнения, без дополнительных вызовов модели). Замечание остаётся в любом
случае; непроверенная цитата просто не показывается как цитата.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from services.schemas import Issue

# Слишком короткая цитата («руб.», «п. 4») встречается в любом документе и ничего не доказывает.
MIN_EVIDENCE_CHARS = 10
# Цитата должна быть короткой: длинная — это пересказ целого абзаца.
MAX_EVIDENCE_CHARS = 300

_WRAPPING_QUOTES = {"«": "»", "“": "”", "„": "“", "‘": "’", '"': '"', "'": "'"}


@dataclass(frozen=True)
class GroundedIssue:
    issue: Issue
    # Цитата в том виде, как она найдена в тексте, либо None: модель её не дала или она в тексте не найдена.
    verified_evidence: str | None


def normalize(text: str) -> str:
    """Единственная допустимая нормализация: любые последовательности пробельных символов -> один пробел."""
    return " ".join(text.split())


def _unwrap(quote: str) -> str:
    """Модель часто берёт цитату в кавычки; сами кавычки в документе — не часть цитаты."""
    if len(quote) >= 2 and _WRAPPING_QUOTES.get(quote[0]) == quote[-1]:
        return quote[1:-1].strip()
    return quote


def verify_evidence(evidence: str | None, normalized_source: str) -> str | None:
    """Возвращает цитату, если она дословно входит в уже нормализованный текст документа, иначе None."""
    if evidence is None:
        return None
    quote = _unwrap(normalize(evidence))
    if not MIN_EVIDENCE_CHARS <= len(quote) <= MAX_EVIDENCE_CHARS:
        return None
    return quote if quote in normalized_source else None


def ground_issues(issues: Iterable[Issue], source_text: str) -> list[GroundedIssue]:
    """`source_text` — извлечённый текст документа (без служебной пометки об охвате, которую добавляет код)."""
    normalized_source = normalize(source_text)
    return [GroundedIssue(issue, verify_evidence(issue.evidence, normalized_source)) for issue in issues]
