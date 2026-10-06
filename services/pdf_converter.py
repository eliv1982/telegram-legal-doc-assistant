"""
Работа с PDF: текстовый слой по страницам и рендеринг страниц-сканов в изображения для Vision (Poppler).

Всё здесь блокирующее (pypdf, subprocess Poppler): вызывать через asyncio.to_thread.
Какие страницы читать и как объяснять охват — решает services.document_extraction, не этот модуль.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError
from pypdf import PdfReader

from services import limits
from services.validation import Rejection, normalize_image

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """Страницу не удалось подготовить к анализу; reason — одна из категорий Rejection (RENDER_*)."""

    def __init__(self, reason: Rejection):
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class RenderedPage:
    jpeg: bytes  # нормализованное изображение страницы для Vision
    blank: bool  # на странице нет содержимого: Vision на неё тратить не нужно


def extract_page_texts(path: Path, pages: Sequence[int]) -> dict[int, str]:
    """Текстовый слой указанных страниц (нумерация с 1). Страница, чей текст не извлёкся, даёт пустую строку."""
    reader = PdfReader(path)
    texts: dict[int, str] = {}
    for number in pages:
        try:
            raw = reader.pages[number - 1].extract_text() or ""
        except Exception as exc:  # битый поток одной страницы не должен ронять разбор остальных
            logger.info("Text extraction failed on one page: %s", type(exc).__name__)
            raw = ""
        # Одиночные суррогаты, которые бывают в испорченных PDF, ломают кодирование запроса к API.
        texts[number] = raw.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8").strip()
    return texts


def render_pages(path: Path, pages: Sequence[int]) -> dict[int, RenderedPage]:
    """
    Рендерит страницы (нумерация с 1) через Poppler. Размер растра ограничен сверху `-scale-to`:
    длина длинной стороны не больше RENDER_LONG_SIDE_PX, что бы ни было написано в MediaBox.
    Каждый вызов Poppler ограничен RENDER_TIMEOUT_SECONDS. Бросает RenderError.
    Не установленный Poppler (PDFInfoNotInstalledError) — сбой окружения, а не вина файла: не перехватывается.
    """
    rendered: dict[int, RenderedPage] = {}
    if not pages:
        return rendered
    timeout = limits.RENDER_TIMEOUT_SECONDS
    try:
        # convert_from_path (pdf2image 1.17) запускает pdfinfo без тайм-аута. Тот же вызов с тайм-аутом делаем сами:
        # если разбор зависает, он будет убит здесь, а внутренний вызов не дойдёт до зависающего файла.
        pdfinfo_from_path(path, timeout=timeout)
        for number in pages:
            images = convert_from_path(
                path,
                dpi=72,  # запасной предел; фактический размер задаёт size (-scale-to имеет приоритет)
                size=limits.RENDER_LONG_SIDE_PX,
                first_page=number,
                last_page=number,
                timeout=timeout,
            )
            rendered[number] = _checked_page(images)
    except PDFPopplerTimeoutError:
        raise RenderError(Rejection.RENDER_TIMEOUT) from None
    except (PDFPageCountError, PDFSyntaxError):
        raise RenderError(Rejection.RENDER_FAILED) from None
    return rendered


def _checked_page(images: list) -> RenderedPage:
    """Результат Poppler тоже недоверенный: пустой список или полоска в несколько пикселей — это сбой рендеринга."""
    if not images:
        raise RenderError(Rejection.RENDER_FAILED)
    image = images[0]
    if min(image.size) < limits.MIN_RENDER_SIDE_PX:
        raise RenderError(Rejection.RENDER_FAILED)
    darkest, lightest = image.convert("L").getextrema()
    return RenderedPage(jpeg=normalize_image(image), blank=lightest - darkest <= limits.BLANK_CONTRAST)
