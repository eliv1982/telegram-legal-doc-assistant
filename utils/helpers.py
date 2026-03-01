"""
Вспомогательные функции.
"""
import re
from typing import Any


def _strip_end_marker(content: str, end_marker: str) -> str:
    """Удаляет маркер окончания === END_* === из конца контента."""
    if end_marker and content.strip().endswith(end_marker.strip()):
        content = content[: -len(end_marker)].strip()
    return content


def parse_response_sections(text: str) -> dict[str, str]:
    """
    Парсит ответ с маркерами === TEXT_REPORT ===, === TTS_SCRIPT ===, === CHECKLIST ===
    и их закрывающими === END_* ===.
    Возвращает словарь с ключами text_report, tts_script, checklist.
    """
    sections: dict[str, str] = {
        "text_report": "",
        "tts_script": "",
        "checklist": "",
    }
    blocks = [
        ("=== TEXT_REPORT ===", "=== END_TEXT_REPORT ===", "text_report"),
        ("=== TTS_SCRIPT ===", "=== END_TTS_SCRIPT ===", "tts_script"),
        ("=== CHECKLIST ===", "=== END_CHECKLIST ===", "checklist"),
    ]
    for start_marker, end_marker, key in blocks:
        idx = text.find(start_marker)
        if idx >= 0:
            start = idx + len(start_marker)
            # Сначала ищем END-маркер, иначе — следующий блок
            end_idx = text.find(end_marker, start)
            if end_idx >= start:
                content = text[start:end_idx].strip()
            else:
                # Fallback: до следующего блока
                end = len(text)
                for sm, _, _ in blocks:
                    if sm != start_marker:
                        ni = text.find(sm, start)
                        if ni > start:
                            end = ni
                            break
                content = text[start:end].strip()
                content = _strip_end_marker(content, end_marker)
            sections[key] = content
    return sections


def parse_confidence(value: Any) -> int:
    """
    Парсит confidence из анализа (строка "85%", число 85 и т.д.).
    :return: число 0–100 или 100 при ошибке
    """
    if value is None:
        return 100
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    s = str(value).strip().rstrip("%")
    try:
        return max(0, min(100, int(float(s))))
    except ValueError:
        return 100


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """
    Извлекает JSON из текста (на случай если модель обернула в markdown).
    """
    import json

    # Попытка найти JSON-блок
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None
