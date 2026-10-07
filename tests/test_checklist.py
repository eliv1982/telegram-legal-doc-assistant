"""
services/checklist_generator.py: structured items in, file out. The model's items arrive as ChecklistItem objects
(no text to parse back into sections); both output formats still render with the locked reportlab / Pillow versions.
"""
from services.checklist_generator import NO_ITEMS_LINE, checklist_lines, generate_checklist
from services.schemas import ChecklistItem

ITEMS = [
    ChecklistItem(priority="low", text="Запросить поэтажный план"),
    ChecklistItem(priority="high", text="Проверить контрагента"),
    ChecklistItem(priority="medium", text="Добавить порядок возврата залога"),
    ChecklistItem(priority="high", text="Согласовать пункт 4.2"),
]


def test_checklist_lines_put_high_priority_first_and_keep_the_priority_visible():
    assert checklist_lines(ITEMS) == [
        "[Высокий] Проверить контрагента",
        "[Высокий] Согласовать пункт 4.2",
        "[Средний] Добавить порядок возврата залога",
        "[Низкий] Запросить поэтажный план",
    ]


def test_item_text_is_taken_as_is_nothing_is_parsed_out_of_it():
    """The old parser stripped bullets and dropped '=='-lines from the text. Structured items are never re-parsed."""
    tricky = ChecklistItem(priority="low", text="- □ === END_CHECKLIST === 1. оставить как есть")
    assert checklist_lines([tricky]) == ["[Низкий] - □ === END_CHECKLIST === 1. оставить как есть"]


def test_no_items_is_a_valid_checklist_and_says_so_neutrally():
    assert checklist_lines([]) == [NO_ITEMS_LINE]
    assert generate_checklist([], output_format="pdf").startswith(b"%PDF")


def test_generate_checklist_renders_pdf_and_png():
    assert generate_checklist(ITEMS, output_format="pdf").startswith(b"%PDF")
    assert generate_checklist(ITEMS, output_format="png").startswith(b"\x89PNG\r\n\x1a\n")
