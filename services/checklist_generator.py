"""
Генерация файла чек-листа: PDF или изображение.
"""
import io
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from services.schemas import PRIORITY_LABELS, PRIORITY_ORDER, ChecklistItem

logger = logging.getLogger(__name__)

# Регистрация шрифта для кириллицы (пробуем системные пути)
_CYRILLIC_FONT_REGISTERED = False
for _path in [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]:
    try:
        pdfmetrics.registerFont(TTFont("CyrillicFont", _path))
        _CYRILLIC_FONT_REGISTERED = True
        break
    except Exception:
        continue

# Символ для чекбокса
CHECKBOX = "☐"


NO_ITEMS_LINE = "Пунктов для проверки модель не предложила."


def checklist_lines(items: Sequence[ChecklistItem]) -> list[str]:
    """
    Строки чек-листа из структурных пунктов модели: сначала высокий приоритет, затем средний и низкий.
    Приоритет — оценка модели, поэтому он остаётся видимой меткой пункта.
    """
    ordered = sorted(items, key=lambda item: PRIORITY_ORDER.index(item.priority))  # сортировка устойчива
    lines = [f"[{PRIORITY_LABELS[item.priority]}] {item.text}" for item in ordered]
    return lines or [NO_ITEMS_LINE]


def generate_checklist_pdf(items: Sequence[ChecklistItem], output_path: str | Path | None = None) -> bytes:
    """
    Создаёт PDF с чек-листом.
    :param items: структурные пункты чек-листа
    :param output_path: необязательно — путь для сохранения
    :return: байты PDF
    """
    lines = checklist_lines(items)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x, y = 50, height - 50
    line_height = 24
    font_name = "CyrillicFont" if _CYRILLIC_FONT_REGISTERED else "Helvetica"
    c.setFont(font_name, 12)
    for item in lines:
        if y < 50:
            c.showPage()
            c.setFont(font_name, 12)
            y = height - 50
        text_line = f"{CHECKBOX} {item}"
        if len(text_line) > 90:
            text_line = text_line[:87] + "..."
        c.drawString(x, y, text_line)
        y -= line_height
    c.save()
    pdf_bytes = buf.getvalue()
    buf.seek(0)
    if output_path:
        Path(output_path).write_bytes(pdf_bytes)
    return pdf_bytes


def generate_checklist_image(items: Sequence[ChecklistItem], output_path: str | Path | None = None) -> bytes:
    """
    Создаёт PNG-изображение с чек-листом.
    :return: байты PNG
    """
    lines = checklist_lines(items)
    line_height = 32
    padding = 40
    width = 600
    height = padding * 2 + len(lines) * line_height

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Кроссплатформенный выбор шрифта
    font_paths = [
        "arial.ttf",
        "Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font = None
    for fp in font_paths:
        try:
            font = ImageFont.truetype(fp, 18)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    y = padding
    for item in lines:
        text_line = f"{CHECKBOX} {item}"
        if len(text_line) > 70:
            text_line = text_line[:67] + "..."
        draw.text((padding, y), text_line, fill=(0, 0, 0), font=font)
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    png_bytes = buf.read()
    if output_path:
        Path(output_path).write_bytes(png_bytes)
    return png_bytes


def generate_checklist(
    items: Sequence[ChecklistItem],
    output_format: Literal["pdf", "png"] = "pdf",
    output_path: str | Path | None = None,
) -> bytes:
    """
    Универсальная функция: создаёт чек-лист в указанном формате.
    """
    if output_format == "png":
        return generate_checklist_image(items, output_path)
    return generate_checklist_pdf(items, output_path)
