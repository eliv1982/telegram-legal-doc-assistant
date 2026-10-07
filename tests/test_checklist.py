"""
services/checklist_generator.py: structured items and the failed automatic checks go in, one readable PDF
comes out. PDFs are generated offline and read back with pypdf; there are no pixel comparisons.
"""
import inspect
import io

import pytest
from pypdf import PdfReader
from reportlab.platypus import Paragraph

import config
from services import checklist_generator
from services.checklist_generator import (
    AUTOMATIC_TITLE,
    FONT_DIR,
    FONT_FILES,
    MODEL_TITLE,
    NO_ITEMS_LINE,
    TITLE,
    ChecklistFontError,
    _ChecklistRow,
    _register_fonts,
    _story,
    build_checklist,
    generate_checklist,
    printable,
)
from services.deterministic_checks import CheckStatus, run_deterministic_checks
from services.report import AUTOMATIC_NOTE, AUTOMATIC_OCR_NOTE, describe_check
from services.schemas import ChecklistItem

TRICKY = "- □ === END_CHECKLIST === 1. оставить как есть"
ITEMS = [
    ChecklistItem(priority="low", text="Запросить поэтажный план"),
    ChecklistItem(priority="high", text="Проверить контрагента"),
    ChecklistItem(priority="medium", text="Добавить порядок возврата залога"),
    ChecklistItem(priority="high", text="Согласовать пункт 4.2"),
    ChecklistItem(priority="low", text=TRICKY),
]
# Two failures (ИНН 7707083894, ОГРН 1027700132196), one pass (ИНН 7707083893) and one info line (КПП).
CHECKS = run_deterministic_checks(
    "[Страница 1]\nИНН 7707083894 КПП 773601001\n[Страница 2]\nИНН 7707083893 ОГРН 1027700132196"
)
FAILED = [check for check in CHECKS if check.status is CheckStatus.FAIL]


def pdf_text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_sections_come_from_structure_failed_checks_apart_from_the_models_items():
    automatic, model = build_checklist(ITEMS, CHECKS)

    assert (automatic.title, model.title) == (AUTOMATIC_TITLE, MODEL_TITLE)
    assert len(FAILED) == 2 and [entry.text for entry in automatic.entries] == [describe_check(c) for c in FAILED]
    assert AUTOMATIC_NOTE in automatic.notes and AUTOMATIC_OCR_NOTE not in automatic.notes
    assert AUTOMATIC_OCR_NOTE in build_checklist([], CHECKS, ocr_used=True)[0].notes
    assert not any("7707083893" in entry.text or "773601001" in entry.text for entry in automatic.entries)  # no PASS, no INFO
    assert [(entry.tag, entry.text) for entry in model.entries] == [  # high first, stable within a priority, text untouched
        ("[Высокий]", "Проверить контрагента"),
        ("[Высокий]", "Согласовать пункт 4.2"),
        ("[Средний]", "Добавить порядок возврата залога"),
        ("[Низкий]", "Запросить поэтажный план"),
        ("[Низкий]", TRICKY),
    ]


def test_without_failed_checks_there_is_no_automatic_section_and_no_items_is_said_neutrally():
    passing = run_deterministic_checks("ИНН 7707083893 КПП 773601001")

    assert [section.title for section in build_checklist(ITEMS, passing)] == [MODEL_TITLE]
    assert [section.title for section in build_checklist(ITEMS)] == [MODEL_TITLE]
    empty = build_checklist([])[0]
    assert empty.entries == () and empty.notes == (NO_ITEMS_LINE,)  # a note, not a checkbox
    assert NO_ITEMS_LINE in flat(pdf_text(generate_checklist([])))


def test_the_pdf_has_extractable_cyrillic_titled_sections_and_embeds_the_bundled_font():
    pdf = generate_checklist(ITEMS, CHECKS, ocr_used=True)
    text = flat(pdf_text(pdf))

    for expected in (TITLE, AUTOMATIC_TITLE, MODEL_TITLE, AUTOMATIC_OCR_NOTE, "[Высокий] Проверить контрагента",
                     TRICKY.replace("□", "?"),  # the one character here that the font has no glyph for
                     *(describe_check(check) for check in FAILED)):
        assert expected in text, expected
    assert text.index(AUTOMATIC_TITLE) < text.index(describe_check(FAILED[0])) < text.index(MODEL_TITLE) < text.index("Проверить")
    assert "7707083893" not in text  # the passing check is not listed
    assert generate_checklist(ITEMS, CHECKS, ocr_used=True) == pdf  # same input, same bytes
    fonts = {str(font.get_object()["/BaseFont"]) for page in PdfReader(io.BytesIO(pdf)).pages
             for font in page["/Resources"]["/Font"].values()}
    assert any("PTSans" in name for name in fonts) and any("PTSans-Bold" in name for name in fonts)


def test_long_items_are_wrapped_not_truncated_and_markup_like_text_stays_literal():
    long_item = " ".join(f"условие{n}" for n in range(120))
    token = "Ф" * 200  # wider than the page: split over lines, every character kept
    markup = "<b>жирный</b> & <font color=red>x</font> </para> [a](http://x.y) _a_ *b*"

    pdf = generate_checklist([ChecklistItem(priority="high", text=t) for t in (long_item, token, markup)])
    text = pdf_text(pdf)

    assert flat(long_item) in flat(text)
    assert token in "".join(text.split()) and "..." not in text and "…" not in text
    assert markup in flat(text)


def test_a_long_checklist_paginates_and_an_item_taller_than_a_page_continues_on_the_next():
    items = [ChecklistItem(priority="low", text=f"Пункт {n}: " + "проверить условия оплаты " * 4) for n in range(80)]
    reader = PdfReader(io.BytesIO(generate_checklist(items)))

    assert len(reader.pages) >= 2
    assert all(f"Стр. {number}" in page.extract_text() for number, page in enumerate(reader.pages, start=1))
    text = flat("\n".join(page.extract_text() for page in reader.pages))
    assert all(f"Пункт {n}:" in text for n in range(80))

    huge = " ".join(f"слово{n}" for n in range(4000))
    pages = PdfReader(io.BytesIO(generate_checklist([ChecklistItem(priority="high", text=huge)]))).pages
    assert len(pages) >= 3 and "слово3999" in "\n".join(page.extract_text() for page in pages)


def test_section_headers_are_headers_and_only_the_entries_get_a_checkbox():
    story = _story(build_checklist(ITEMS, CHECKS), _register_fonts())

    rows = [flowable for flowable in story if isinstance(flowable, _ChecklistRow)]
    headings = [flowable.getPlainText() for flowable in story if isinstance(flowable, Paragraph)]
    assert len(rows) == len(FAILED) + len(ITEMS)  # one checkbox per entry: nothing else is a row
    assert {TITLE, AUTOMATIC_TITLE, MODEL_TITLE} <= set(headings)


def test_a_character_the_font_cannot_draw_becomes_a_visible_question_mark_not_an_empty_box():
    glyphs = _register_fonts()

    assert printable("План ⚠️ 🚀 «ё» № 5 — 30 ₽", glyphs) == "План ? ? «ё» № 5 — 30 ₽"
    assert printable("й и т​о\n\tx", glyphs) == "й и то x"  # NFC, NBSP -> space, zero-width dropped
    pdf = generate_checklist([ChecklistItem(priority="low", text="Срок ⚠️ 🚀 указан")])
    assert "Срок ? ? указан" in flat(pdf_text(pdf))


def test_the_bundled_font_is_licensed_and_covers_cyrillic_and_a_missing_font_fails_clearly(monkeypatch, tmp_path):
    assert all((FONT_DIR / name).is_file() for name in FONT_FILES.values())
    assert "SIL OPEN FONT LICENSE" in (FONT_DIR / "OFL.txt").read_text(encoding="utf-8").upper()
    alphabet = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ«»№—…₽0123456789"
    glyphs = _register_fonts()
    assert all(ord(char) in glyphs for char in alphabet)

    monkeypatch.setattr(checklist_generator, "FONT_DIR", tmp_path)  # no fonts here
    _register_fonts.cache_clear()
    with pytest.raises(ChecklistFontError):
        generate_checklist(ITEMS)  # never a silent fall back to a font without Cyrillic
    # a failed registration is not cached, so the next call with the real directory works again (monkeypatch undoes FONT_DIR)


def test_there_is_no_png_checklist_any_more():
    assert not hasattr(checklist_generator, "generate_checklist_image")
    assert not hasattr(config, "CHECKLIST_FORMAT")
    assert "output_format" not in inspect.signature(generate_checklist).parameters
    assert generate_checklist(ITEMS).startswith(b"%PDF")
