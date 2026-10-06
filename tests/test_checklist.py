"""
services/checklist_generator.py: item parsing and a smoke test that both output formats still render
with the locked reportlab / Pillow versions.
"""
from services.checklist_generator import generate_checklist, parse_checklist_text


def test_parse_checklist_text_strips_markers_and_skips_end_marker_lines():
    text = (
        "Чек-лист по договору №15/2025\n"
        "Срочно:\n"
        "□ Проверить контрагента\n"
        "☐ Согласовать пункт 4.2\n"
        "- Запросить план\n"
        "* Добавить возврат залога\n"
        "• Дополнительно\n"
        "\n"
        "=== END_CHECKLIST ==="
    )
    assert parse_checklist_text(text) == [
        "Чек-лист по договору №15/2025",
        "Срочно:",
        "Проверить контрагента",
        "Согласовать пункт 4.2",
        "Запросить план",
        "Добавить возврат залога",
        "Дополнительно",
    ]


def test_generate_checklist_renders_pdf_and_png():
    pdf = generate_checklist("□ Check the counterparty\n□ Agree clause 4.2", output_format="pdf")
    assert pdf.startswith(b"%PDF")

    png = generate_checklist("□ Check the counterparty\n□ Agree clause 4.2", output_format="png")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
