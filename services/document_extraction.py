"""
Политика охвата документа: что именно читается и что честно сообщается пользователю.

PDF: текстовый слой читается первым, постранично. Страница, где текста почти нет (скан), рендерится и
уходит в Vision. Читаются только первые MAX_ANALYSED_PAGES страниц, остальные явно помечаются как
непроанализированные. Изображение — это один «лист», он целиком идёт в Vision.

Порядок внутри extract_document: сначала всё бесплатное и локальное (текстовый слой, рендеринг), и только потом
платные вызовы Vision. Если рендеринг не удался, ни одного платного вызова не будет.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from services import limits, pdf_converter
from services.validation import Rejection, ValidatedDocument

logger = logging.getLogger(__name__)

# Асинхронный распознаватель изображения: OpenAIService.extract_text_from_image(image_bytes, mime_type).
OcrFunc = Callable[[bytes, str], Awaitable[str]]

# Ответ, который DOCUMENT_OCR_PROMPT велит дать, если на изображении нет читаемого текста.
UNREADABLE_MARKER = "[Текст не распознан]"

METHOD_TEXT_LAYER = "text_layer"
METHOD_VISION = "vision"

_MODEL_NOTE_HINT = "Не делай выводов об отсутствии условий или реквизитов, которые могли быть на остальных страницах."


@dataclass(frozen=True)
class DocumentExtractionResult:
    extracted_text: str  # текст для анализа; страницы PDF разделены маркерами «[Страница N]»
    total_pages: int  # всего страниц в файле (1 для изображения)
    analysed_pages: tuple[int, ...]  # страницы, содержимое которых реально попало в extracted_text
    skipped_pages: tuple[int, ...]  # не читались вовсе: за пределами лимита страниц
    unreadable_pages: tuple[int, ...]  # читались, но текста не нашлось (пустая или нераспознанная страница)
    clipped_pages: tuple[int, ...]  # текст страницы обрезан по MAX_PAGE_CHARS
    extraction_methods: tuple[str, ...]  # METHOD_* в порядке первого использования
    is_image: bool

    @property
    def truncated(self) -> bool:
        return bool(self.skipped_pages)

    @property
    def is_complete(self) -> bool:
        return not (self.skipped_pages or self.unreadable_pages or self.clipped_pages)

    @property
    def analysis_text(self) -> str:
        """Текст для модели. При неполном охвате ей сообщается об этом, чтобы она не делала выводов от противного."""
        if self.is_complete:
            return self.extracted_text
        return f"[Охват: {coverage_summary(self)} {_MODEL_NOTE_HINT}]\n\n{self.extracted_text}"


def format_page_ranges(pages: Sequence[int]) -> str:
    """(1, 2, 3, 5) -> «1–3, 5»."""
    ranges: list[str] = []
    start = previous = None
    for page in sorted(pages):
        if start is None:
            start = previous = page
        elif page == previous + 1:
            previous = page
        else:
            ranges.append(_range(start, previous))
            start = previous = page
    if start is not None:
        ranges.append(_range(start, previous))
    return ", ".join(ranges)


def _range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}–{end}"


def coverage_summary(result: DocumentExtractionResult) -> str:
    """Что реально проанализировано. Формируется кодом, а не моделью; спокойный информационный тон."""
    if result.is_image:
        parts = ["Проанализировано: загруженное изображение."]
        if result.clipped_pages:
            parts.append("Текст изображения сокращён по лимиту длины.")
        return " ".join(parts)

    analysed = format_page_ranges(result.analysed_pages)
    if result.skipped_pages:
        parts = [
            f"Проанализированы только страницы {analysed} из {result.total_pages}.",
            f"Страницы {format_page_ranges(result.skipped_pages)} в анализ не вошли.",
        ]
    else:
        parts = [f"Проанализированные страницы: {analysed} из {result.total_pages}."]
    if result.unreadable_pages:
        parts.append(f"Текст не распознан (страницы: {format_page_ranges(result.unreadable_pages)}).")
    if result.clipped_pages:
        parts.append(f"Текст сокращён по лимиту длины (страницы: {format_page_ranges(result.clipped_pages)}).")
    return " ".join(parts)


def format_coverage(result: DocumentExtractionResult) -> str:
    """Строка охвата, которую код дописывает к отчёту."""
    return f"ℹ️ {coverage_summary(result)}"


async def extract_document(doc: ValidatedDocument, ocr: OcrFunc) -> DocumentExtractionResult | Rejection:
    """
    Извлекает текст проверенного документа. Блокирующая работа (pypdf, Poppler) вынесена в потоки.
    Возвращает Rejection, если документ невозможно подготовить (рендеринг) или в нём нет читаемого текста.
    Ошибки самого ocr (OpenAI) не перехватываются: это временные сбои, а не свойство файла.
    """
    if doc.kind == "image":
        return await _extract_image(doc, ocr)
    return await _extract_pdf(doc, ocr)


def _clean_ocr(text: str) -> str:
    cleaned = text.strip()
    return "" if cleaned == UNREADABLE_MARKER else cleaned


def _clip(text: str) -> tuple[str, bool]:
    return text[: limits.MAX_PAGE_CHARS], len(text) > limits.MAX_PAGE_CHARS


async def _extract_image(doc: ValidatedDocument, ocr: OcrFunc) -> DocumentExtractionResult | Rejection:
    text = _clean_ocr(await ocr(doc.image_jpeg, "image/jpeg"))
    if not text:
        return Rejection.NO_TEXT
    text, clipped = _clip(text)
    return DocumentExtractionResult(
        extracted_text=text,
        total_pages=1,
        analysed_pages=(1,),
        skipped_pages=(),
        unreadable_pages=(),
        clipped_pages=(1,) if clipped else (),
        extraction_methods=(METHOD_VISION,),
        is_image=True,
    )


async def _extract_pdf(doc: ValidatedDocument, ocr: OcrFunc) -> DocumentExtractionResult | Rejection:
    budget = list(range(1, min(doc.page_count, limits.MAX_ANALYSED_PAGES) + 1))
    skipped = tuple(range(len(budget) + 1, doc.page_count + 1))

    # 1. Бесплатно и локально: текстовый слой всех страниц из бюджета.
    layer = await asyncio.to_thread(pdf_converter.extract_page_texts, doc.path, budget)
    scanned = [n for n in budget if len("".join(layer[n].split())) < limits.MIN_PAGE_TEXT_CHARS]

    # 2. Бесплатно и локально: рендеринг только страниц-сканов. Сбой здесь происходит до любого платного вызова.
    try:
        rendered = await asyncio.to_thread(pdf_converter.render_pages, doc.path, scanned) if scanned else {}
    except pdf_converter.RenderError as exc:
        logger.info("PDF rendering rejected: %s", exc.reason.value)
        return exc.reason

    # 3. Платно: Vision по одной странице, в порядке нумерации, только для непустых сканов.
    blocks: list[str] = []
    analysed: list[int] = []
    unreadable: list[int] = []
    clipped: list[int] = []
    methods: list[str] = []
    for number in budget:
        text, method = layer[number], METHOD_TEXT_LAYER
        page = rendered.get(number)
        if page is not None and not page.blank:
            vision_text = _clean_ocr(await ocr(page.jpeg, "image/jpeg"))
            if vision_text:
                text, method = vision_text, METHOD_VISION
        if not text:
            unreadable.append(number)
            continue
        text, was_clipped = _clip(text)
        blocks.append(f"[Страница {number}]\n{text}")
        analysed.append(number)
        if was_clipped:
            clipped.append(number)
        if method not in methods:
            methods.append(method)

    if not analysed:
        return Rejection.NO_TEXT
    logger.info(
        "Extracted %d of %d pages (%s), %d chars",
        len(analysed), doc.page_count, "+".join(methods), sum(len(b) for b in blocks),
    )
    return DocumentExtractionResult(
        extracted_text="\n\n".join(blocks),
        total_pages=doc.page_count,
        analysed_pages=tuple(analysed),
        skipped_pages=skipped,
        unreadable_pages=tuple(unreadable),
        clipped_pages=tuple(clipped),
        extraction_methods=tuple(methods),
        is_image=False,
    )
