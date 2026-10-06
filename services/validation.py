"""
Проверка загруженных файлов до любых платных вызовов.

Расширение файла не является доказательством: тип определяется по содержимому, изображение реально
декодируется Pillow, PDF проходит структурную проверку pypdf. Результат — типизированный:
либо ValidatedDocument, либо Rejection (причина отказа). Исключения для плохого ввода не используются.

Нет файла на диске (FileNotFoundError) — это не ошибка пользователя, а сбой окружения: исключение не перехватывается.
"""
import io
import logging
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps
from pypdf import PdfReader

from services import limits

logger = logging.getLogger(__name__)


class Rejection(Enum):
    """Причины, по которым файл не принимается. Все — свойства самого файла, повторять отправку бессмысленно."""

    UNSUPPORTED_TYPE = "unsupported_type"  # не PDF и не JPEG/PNG/WEBP
    EMPTY = "empty"
    TOO_LARGE = "too_large"  # размер файла в байтах
    CORRUPT_PDF = "corrupt_pdf"  # не читается, нет страниц
    ENCRYPTED_PDF = "encrypted_pdf"
    TOO_MANY_PAGES = "too_many_pages"
    PAGE_SIZE = "page_size"  # физические размеры страницы PDF вне допустимых
    UNREADABLE_IMAGE = "unreadable_image"  # не декодируется или слишком мала
    IMAGE_SIZE = "image_size"  # слишком много пикселей
    RENDER_FAILED = "render_failed"
    RENDER_TIMEOUT = "render_timeout"
    NO_TEXT = "no_text"  # ни одна страница не дала читаемого текста


@dataclass(frozen=True)
class ValidatedDocument:
    path: Path
    kind: Literal["pdf", "image"]
    page_count: int  # 1 для изображения
    image_jpeg: bytes | None = None  # только для kind == "image": нормализованное изображение для Vision


_IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PDF_MAGIC = b"%PDF-"
_SNIFF_BYTES = 1024  # заголовок PDF допускается в первом килобайте


def exceeds_upload_limit(declared_size: int | None) -> bool:
    """Размер, заявленный Telegram до скачивания. None — неизвестен, решит проверка после скачивания."""
    return declared_size is not None and declared_size > limits.MAX_UPLOAD_BYTES


def check_file(path: Path) -> Rejection | None:
    """Общие проверки любого загруженного файла: не пустой, не больше лимита."""
    size = path.stat().st_size
    if size == 0:
        return Rejection.EMPTY
    if size > limits.MAX_UPLOAD_BYTES:
        return Rejection.TOO_LARGE
    return None


def validate_voice(path: Path) -> Rejection | None:
    """Голос проверяется только по размеру: формат и длительность решает Whisper."""
    return check_file(path)


def validate_document(path: Path) -> ValidatedDocument | Rejection:
    """Определяет тип по содержимому и проверяет файл. Блокирующая: вызывать через asyncio.to_thread."""
    rejection = check_file(path)
    if rejection is not None:
        return rejection

    with path.open("rb") as fh:
        head = fh.read(_SNIFF_BYTES)
    if head.startswith((_JPEG_MAGIC, _PNG_MAGIC)) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return _validate_image(path)
    if _PDF_MAGIC in head:
        return _validate_pdf(path)
    return Rejection.UNSUPPORTED_TYPE


def _validate_image(path: Path) -> ValidatedDocument | Rejection:
    try:
        with Image.open(path, formats=_IMAGE_FORMATS) as img:
            # Размер известен по заголовку: слишком большое не декодируем вовсе (защита от «бомб» декомпрессии).
            width, height = img.size
            if width * height > limits.MAX_IMAGE_PIXELS:
                return Rejection.IMAGE_SIZE
            if min(width, height) < limits.MIN_IMAGE_SIDE_PX:
                return Rejection.UNREADABLE_IMAGE
            # JPEG можно декодировать сразу в уменьшенном виде (не меньше MAX_IMAGE_SIDE_PX по обеим сторонам):
            # так большой снимок не занимает в памяти полный размер. Для остальных форматов вызов ничего не делает.
            img.draft("RGB", (limits.MAX_IMAGE_SIDE_PX, limits.MAX_IMAGE_SIDE_PX))
            img.load()  # реальное декодирование: обрезанный или битый файл падает здесь
            jpeg = normalize_image(img)
    except Image.DecompressionBombError:
        return Rejection.IMAGE_SIZE
    except Exception as exc:  # декодеры Pillow бросают OSError, SyntaxError, ValueError и др.
        logger.info("Image decode failed: %s", type(exc).__name__)
        return Rejection.UNREADABLE_IMAGE
    return ValidatedDocument(path=path, kind="image", page_count=1, image_jpeg=jpeg)


def normalize_image(img: Image.Image) -> bytes:
    """
    Новый RGB-JPEG для Vision: ориентация из EXIF применена, прозрачность залита белым, длинная сторона
    не больше MAX_IMAGE_SIDE_PX. EXIF, ICC и прочие метаданные исходника не переносятся.
    Забирает img себе: изображение меняется на месте, чтобы не держать в памяти лишние копии полного размера
    (у предельного по размеру снимка каждая копия — около 120 МБ).
    """
    ImageOps.exif_transpose(img, in_place=True)
    if img.mode.startswith("I"):  # 16-битные сканы: convert("RGB") превратил бы их в белый лист
        img = img.point(lambda value: value * (1 / 256)).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((limits.MAX_IMAGE_SIDE_PX, limits.MAX_IMAGE_SIDE_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=limits.JPEG_QUALITY)
    return buf.getvalue()


def _validate_pdf(path: Path) -> ValidatedDocument | Rejection:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:  # pypdf без cryptography всё равно не расшифрует AES: отказ явный и для всех
            return Rejection.ENCRYPTED_PDF
        page_count = len(reader.pages)
        if page_count == 0:
            return Rejection.CORRUPT_PDF
        if page_count > limits.MAX_PDF_PAGES:
            return Rejection.TOO_MANY_PAGES
        for page in reader.pages:
            if not _page_size_is_reasonable(page):
                return Rejection.PAGE_SIZE
    except Exception as exc:  # pypdf бросает разные типы на разных повреждениях; пользователю важен факт, не тип
        logger.info("PDF preflight failed: %s", type(exc).__name__)
        return Rejection.CORRUPT_PDF
    return ValidatedDocument(path=path, kind="pdf", page_count=page_count)


def _page_size_is_reasonable(page) -> bool:
    """MediaBox (его и рендерит Poppler), умноженный на /UserUnit: размер страницы в пунктах, как её видит читатель."""
    box = page.mediabox  # нет MediaBox -> ValueError -> повреждённый PDF
    unit = float(page["/UserUnit"]) if "/UserUnit" in page else 1.0
    sides = (abs(float(box.width)) * unit, abs(float(box.height)) * unit)
    if not all(math.isfinite(side) for side in sides):
        return False
    return limits.MIN_PAGE_POINTS <= min(sides) and max(sides) <= limits.MAX_PAGE_POINTS
