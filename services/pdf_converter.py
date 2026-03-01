"""
Конвертация PDF в изображение для передачи в GPT-4 Vision.
Fallback: извлечение текста из PDF через pypdf (без Vision).
"""
import io
import logging
from pathlib import Path

from pdf2image import convert_from_path
from pypdf import PdfReader
from PIL import Image

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """
    Fallback-OCR: извлечение текста из PDF без Vision (для скан-документов даст пустой результат).
    :return: извлечённый текст или пустая строка
    """
    try:
        reader = PdfReader(str(pdf_path))
        text_parts = []
        for page in reader.pages[:5]:  # первые 5 страниц
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n\n".join(text_parts).strip() if text_parts else ""
    except Exception as e:
        logger.warning("Fallback PDF extract failed: %s", e)
        return ""

# DPI для уменьшения размера (Vision имеет ограничения)
DEFAULT_DPI = 200


def pdf_first_page_to_image(pdf_path: str | Path, dpi: int = DEFAULT_DPI) -> bytes:
    """
    Конвертирует первую страницу PDF в JPEG-байты.
    :param pdf_path: путь к PDF-файлу
    :param dpi: разрешение (меньше — меньше размер)
    :return: байты JPEG
    """
    images = convert_from_path(str(pdf_path), dpi=dpi, first_page=1, last_page=1)
    if not images:
        raise ValueError("Не удалось извлечь страницу из PDF")
    img = images[0]
    buf = io.BytesIO()
    # Конвертируем в RGB если нужно (некоторые PDF дают RGBA)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return buf.read()


def image_to_bytes(image_path: str | Path) -> tuple[bytes, str]:
    """
    Загружает изображение и возвращает байты + расширение.
    :return: (bytes, extension) например (b'...', 'png')
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    ext = path.suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError(f"Неподдерживаемый формат изображения: {ext}")
    with open(path, "rb") as f:
        data = f.read()
    return data, ext
