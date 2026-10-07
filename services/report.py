"""
Сборка сообщений для пользователя. Всё, что нельзя доверять модели, добавляется здесь, кодом, и после сокращения
текста модели, поэтому не может потеряться:

  автоматические проверки реквизитов (код, не модель)  ->  заголовок «оценка модели»  ->  текст модели (при
  необходимости сокращённый)  ->  цитаты из документа, найденные в тексте дословно  ->  строка охвата
  (детерминированные данные извлечения)  ->  дисклеймер.

Четыре вида содержимого различимы: автоматические проверки — результат кода по формату и контрольным числам; охват —
факт об извлечении; текст модели — её оценка; цитата показывается как цитата только после проверки по тексту
документа (services.grounding). Результаты проверок модель не видит и не переписывает.

Сообщения уходят обычным текстом, без режима разметки Telegram (Markdown/HTML): текст модели, цитаты и значения
реквизитов не нужно экранировать, и ни одна из их строк не может ни сломать отправку, ни создать форматирование.
"""
from collections.abc import Sequence

from services import limits
from services.deterministic_checks import CheckStatus, DeterministicCheck
from services.grounding import GroundedIssue
from services.schemas import PRIORITY_LABELS
from utils.text import fit_text, fit_words

AUTOMATIC_HEADER = "🔎 Автоматические проверки реквизитов (код, без участия ИИ)"
AUTOMATIC_NOTE = (
    "Автоматические проверки оценивают только формат и контрольные признаки и не подтверждают существование "
    "или статус организации, принадлежность реквизита стороне и юридическую силу документа."
)
AUTOMATIC_OCR_NOTE = "Текст сканов распознан ИИ: ошибка распознавания может дать ложное несовпадение."
AUTOMATIC_FAILED = (
    "🔎 Автоматические проверки реквизитов не выполнены из-за внутренней ошибки. "
    "Оценка модели ниже от них не зависит."
)
# Блок ограничен, чтобы отчёту модели всегда оставалось место; сначала показываются непрошедшие проверки.
MAX_CHECKS_CHARS = 1300
_STATUS_ICONS = {CheckStatus.PASS: "✅", CheckStatus.FAIL: "❌", CheckStatus.INFO: "ℹ️"}
_STATUS_ORDER = (CheckStatus.FAIL, CheckStatus.PASS, CheckStatus.INFO)
_MORE_RESERVE = 70  # место под строку «…и ещё проверок: N, из них не прошли: K.»

REPORT_HEADER = "🤖 Предварительный разбор: оценка модели ИИ"
DISCLAIMER = (
    "⚠️ Это автоматический предварительный разбор (first-pass review), а не юридическая консультация. "
    "Оценку выполнила модель ИИ: она может ошибаться и не заменяет проверку юристом."
)
TTS_DISCLAIMER = "Это предварительная оценка модели, а не юридическая консультация."
# Автоматические проверки вслух не зачитываются: достаточно одной фразы, и только если какая-то проверка не прошла.
TTS_AUTOMATIC_FAILED = "Автоматические проверки реквизитов выявили замечания."

QUOTES_HEADER = "📎 Цитаты из документа (найдены в его тексте дословно):"
# Блок цитат ограничен, чтобы отчёту модели всегда оставалось место.
MAX_QUOTES_CHARS = 1200
ELLIPSIS = "…"
# Пометка в конце сокращённого текста модели: пользователь должен видеть, что это не весь текст.
SHORTENED_MARKER = f"\n{ELLIPSIS} (сокращено по лимиту сообщения Telegram)"

_SEPARATOR = "\n\n"


def _quotes_block(findings: Sequence[GroundedIssue]) -> str:
    quoted = [finding for finding in findings if finding.verified_evidence]
    lines: list[str] = []
    if quoted:
        lines.append(QUOTES_HEADER)
        used = len(QUOTES_HEADER)
        for index, finding in enumerate(quoted):
            label = PRIORITY_LABELS[finding.issue.priority]
            line = f"• [{label}] {finding.issue.title}: «{finding.verified_evidence}»"
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


def describe_check(check: DeterministicCheck) -> str:
    """Результат проверки одной строкой без значка статуса. Один текст и для отчёта, и для чек-листа."""
    pages = f" (стр. {', '.join(map(str, check.pages))})" if check.pages else ""
    return f"{check.label} {check.value}{pages} — {check.message}"


def _check_line(check: DeterministicCheck) -> str:
    return f"{_STATUS_ICONS[check.status]} {describe_check(check)}"


def format_automatic_checks(checks: Sequence[DeterministicCheck] | None, *, ocr_used: bool = False) -> str:
    """
    Раздел «Автоматические проверки»: строки пишет код по результатам services.deterministic_checks, модель их не
    видит. Непрошедшие проверки идут первыми, остальные — в порядке появления в документе; если места не хватает,
    сообщается, сколько проверок (и сколько из них непрошедших) не показано. Пустой список — реквизитов с метками
    нет, раздела нет. None — слой проверок не отработал из-за внутренней ошибки: об этом сказано отдельной строкой.
    """
    if checks is None:
        return AUTOMATIC_FAILED
    if not checks:
        return ""
    note = f"{AUTOMATIC_NOTE} {AUTOMATIC_OCR_NOTE}" if ocr_used else AUTOMATIC_NOTE
    ordered = sorted(checks, key=lambda check: _STATUS_ORDER.index(check.status))  # сортировка устойчива
    available = MAX_CHECKS_CHARS - len(AUTOMATIC_HEADER) - len(note) - _MORE_RESERVE - len(_SEPARATOR)
    lines: list[str] = []
    used = 0
    for check in ordered:
        line = _check_line(check)
        if used + len(line) + 1 > available:
            break
        lines.append(line)
        used += len(line) + 1
    hidden = ordered[len(lines):]
    if hidden:
        failed = sum(1 for check in hidden if check.status is CheckStatus.FAIL)
        lines.append(f"…и ещё проверок: {len(hidden)}" + (f", из них не прошли: {failed}." if failed else "."))
    return _SEPARATOR.join(["\n".join([AUTOMATIC_HEADER, *lines]), note])


def compose_report(
    model_text: str, findings: Sequence[GroundedIssue], coverage: str, *, limit: int, automatic: str = ""
) -> str:
    """
    Сообщение с отчётом не длиннее `limit`. Сокращается только текст модели: раздел автоматических проверок
    (`automatic`, готовый текст format_automatic_checks), заголовок, цитаты, охват и дисклеймер добавляются целиком
    (их размер ограничен, см. MAX_CHECKS_CHARS и MAX_QUOTES_CHARS). Текст модели обрывается по границе абзаца,
    строки, предложения или слова и получает пометку о сокращении.
    """
    quotes = _quotes_block(findings)
    footer = f"{coverage}{_SEPARATOR}{DISCLAIMER}"
    head = [automatic] if automatic else []
    fixed = [*head, REPORT_HEADER, *([quotes] if quotes else []), footer]
    budget = limit - sum(len(part) for part in fixed) - len(_SEPARATOR) * len(fixed)
    body = fit_text(model_text.strip(), budget, marker=SHORTENED_MARKER)
    return _SEPARATOR.join([*head, REPORT_HEADER, body, *([quotes] if quotes else []), footer])


def compose_tts_script(model_script: str, *, automatic_failed: bool = False) -> str:
    """
    Текст для озвучки: пересказ модели в пределах бюджета limits.TTS_MAX_WORDS (обрыв по границе предложения, слово
    не режется) и фиксированные пометки кода. Пометки добавляются после сокращения и не теряются: о предварительной
    оценке модели — всегда, об автоматических проверках — одной фразой и только если какая-то проверка не прошла
    (`automatic_failed`). Сами проверки вслух не зачитываются, выводов код не делает.
    """
    suffix = " ".join([TTS_AUTOMATIC_FAILED, TTS_DISCLAIMER] if automatic_failed else [TTS_DISCLAIMER])
    summary = fit_words(" ".join(model_script.split()), limits.TTS_MAX_WORDS)
    summary = fit_text(summary, limits.TTS_INPUT_LIMIT - len(suffix) - 1)  # страховка от предела API; обычно не срабатывает
    if summary and summary[-1] not in ".!?…":
        summary += "."  # пометка начинается с новой фразы
    return f"{summary} {suffix}"
