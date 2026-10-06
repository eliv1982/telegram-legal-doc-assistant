"""
Generated sample files for the ingestion tests: valid ones and hostile ones, all built in memory.
Nothing binary is committed, and nothing here needs Poppler to be *built* (only some tests need it to be rendered).
"""
import io
import struct
import zlib

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import FloatObject, NameObject
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def page_text(number: int) -> str:
    """Enough text to count as a real text layer (well above limits.MIN_PAGE_TEXT_CHARS), with a unique marker."""
    return f"CLAUSE-{number}: the parties agree on payment terms, delivery dates and liability for late delivery."


def drawn_image(size: tuple[int, int] = (320, 240)) -> Image.Image:
    """An image with real contrast (so it is not 'blank')."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for y in range(10, size[1] - 10, 20):
        draw.rectangle([10, y, size[0] - 10, y + 6], fill="black")
    return img


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (320, 240), **save_kwargs) -> bytes:
    buf = io.BytesIO()
    drawn_image(size).save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def png_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    return image_bytes("PNG", size)


def jpeg_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    return image_bytes("JPEG", size)


def webp_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    return image_bytes("WEBP", size)


def png_declaring(width: int, height: int) -> bytes:
    """A 45-byte PNG that only *declares* these dimensions: the image twin of the hostile tiny-PDF audit case."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def pdf_bytes(pages: list[str | None]) -> bytes:
    """
    One PDF page per item: a string becomes a page with that text layer, None becomes a 'scanned' page
    (an embedded picture and no text layer at all).
    """
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    for text in pages:
        if text is None:
            pdf.drawImage(ImageReader(drawn_image((600, 800))), 40, 40, width=515, height=760)
        else:
            for row, line in enumerate(text.split("\n")):
                pdf.drawString(72, 750 - 16 * row, line)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def text_pdf(page_count: int = 1) -> bytes:
    return pdf_bytes([page_text(n) for n in range(1, page_count + 1)])


def blank_pdf(page_count: int = 1, width: float = 612, height: float = 792) -> bytes:
    """Pages with no content at all (no text, nothing drawn)."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=width, height=height)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def pdf_with_page_box(width: float, height: float, user_unit: float | None = None) -> bytes:
    """A tiny file that merely *declares* these page dimensions: the shape of the audit's hostile PDF."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    if user_unit is not None:
        page[NameObject("/UserUnit")] = FloatObject(user_unit)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def encrypted_pdf() -> bytes:
    """Password-protected PDF (RC4, which pypdf can write without the `cryptography` package)."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
