"""
Генерация файла чек-листа: PDF или изображение.
"""
import io
import logging
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

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


def parse_checklist_text(text: str) -> list[str]:
    """
    Извлекает пункты чек-листа из текста.
    Поддерживает форматы: □ пункт, ☐ пункт, - пункт, * пункт, 1. пункт
    """
    lines = text.strip().split("\n")
    items: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for prefix in ("□", "☐", "☑", "-", "*", "•"):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if line and not line.startswith("=="):
            items.append(line)
    return items or [text[:200]]


def generate_checklist_pdf(checklist_text: str, output_path: str | Path | None = None) -> bytes:
    """
    Создаёт PDF с чек-листом.
    :param checklist_text: текст чек-листа (с маркерами □)
    :param output_path: необязательно — путь для сохранения
    :return: байты PDF
    """
    items = parse_checklist_text(checklist_text)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x, y = 50, height - 50
    line_height = 24
    font_name = "CyrillicFont" if _CYRILLIC_FONT_REGISTERED else "Helvetica"
    c.setFont(font_name, 12)
    for item in items:
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


def generate_checklist_image(checklist_text: str, output_path: str | Path | None = None) -> bytes:
    """
    Создаёт PNG-изображение с чек-листом.
    :return: байты PNG
    """
    items = parse_checklist_text(checklist_text)
    line_height = 32
    padding = 40
    width = 600
    height = padding * 2 + len(items) * line_height

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
    for item in items:
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
    checklist_text: str,
    output_format: Literal["pdf", "png"] = "pdf",
    output_path: str | Path | None = None,
) -> bytes:
    """
    Универсальная функция: создаёт чек-лист в указанном формате.
    """
    if output_format == "png":
        return generate_checklist_image(checklist_text, output_path)
    return generate_checklist_pdf(checklist_text, output_path)
