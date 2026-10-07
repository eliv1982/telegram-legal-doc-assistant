"""
Рамка для данных в промптах. Задача пользователя и текст документа передаются в отдельных тегах, а не в кавычках:
кавычки внутри документа ничего не ограничивают, а тег с известным именем можно экранировать.
"""
import re

TASK_TAG = "user_task"
DOCUMENT_TAG = "document"
ANALYSIS_TAG = "analysis"

_FENCE_TAGS = (TASK_TAG, DOCUMENT_TAG, ANALYSIS_TAG)
# Открывающий или закрывающий тег нашей рамки в любой записи (регистр, пробелы, атрибуты). `<` заменяется на `&lt;`,
# поэтому документ не может ни закрыть свой блок, ни открыть чужой.
_FENCE_TAG_RE = re.compile(r"<(\s*/?\s*(?:" + "|".join(_FENCE_TAGS) + r")\b)", re.IGNORECASE)


def neutralize(text: str) -> str:
    return _FENCE_TAG_RE.sub(r"&lt;\1", text)


def fence(tag: str, text: str) -> str:
    """Блок данных: <tag>\\n...\\n</tag>. Содержимое не может содержать теги рамки."""
    return f"<{tag}>\n{neutralize(text)}\n</{tag}>"
