"""
Типизированный вид того, что возвращают модели (structured output Chat Completions). Только поля, которые
реально использует пайплайн: ни числовой «уверенности», ни прочих следов старого свободного формата.

Непустота строк проверяется здесь, в коде, а не ограничениями JSON Schema: строгий диалект structured output
в API поддерживает лишь часть ключевых слов, и непроверяемая offline схема — лишний риск.
Все значения — оценка модели, а не установленный факт.
"""
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator

# Приоритет — оценка модели по шкале из промпта, а не юридическая классификация.
Priority = Literal["high", "medium", "low"]
PRIORITY_ORDER: tuple[Priority, ...] = ("high", "medium", "low")
PRIORITY_LABELS: dict[Priority, str] = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}


def _non_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


NonBlank = Annotated[str, AfterValidator(_non_blank)]


class KeyFact(BaseModel):
    name: NonBlank = Field(description="Название реквизита или условия, например «Срок действия» или «Цена».")
    value: NonBlank = Field(description="Значение, как оно указано в документе, или «не указано».")


class Issue(BaseModel):
    priority: Priority = Field(description="Приоритет замечания по шкале из инструкции: high, medium или low.")
    title: NonBlank = Field(description="Короткое название замечания.")
    description: NonBlank = Field(description="В чём замечание и почему оно важно.")
    evidence: str | None = Field(
        description="Короткая ДОСЛОВНАЯ цитата из документа, на которой основано замечание, или null."
    )

    @field_validator("evidence")
    @classmethod
    def _blank_evidence_is_none(cls, value: str | None) -> str | None:
        return value if value is None or value.strip() else None


class AnalysisResult(BaseModel):
    document_type: NonBlank = Field(description="Тип документа, например «Договор аренды нежилого помещения».")
    summary: NonBlank = Field(description="Суть документа в 2–4 предложениях.")
    key_facts: list[KeyFact] = Field(description="Ключевые реквизиты и условия, найденные в документе.")
    issues: list[Issue] = Field(description="Замечания к документу; пустой список, если замечаний не найдено.")


class ChecklistItem(BaseModel):
    priority: Priority = Field(description="Приоритет пункта: high, medium или low.")
    text: NonBlank = Field(description="Одно конкретное действие, одним предложением.")


class ReportResult(BaseModel):
    text_report: NonBlank = Field(description="Текст отчёта для Telegram.")
    tts_script: NonBlank = Field(description="Текст для голосового резюме.")
    checklist_items: list[ChecklistItem] = Field(description="Пункты чек-листа; может быть пустым.")
