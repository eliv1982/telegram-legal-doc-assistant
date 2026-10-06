"""
Upload validation (Stage 3): the type comes from the content, never from the extension, and every
permanent input problem is a typed Rejection decided locally before any paid call.
"""
import io
import logging

import pytest
from PIL import Image

from services import limits
from services.validation import (
    Rejection,
    ValidatedDocument,
    exceeds_upload_limit,
    normalize_image,
    validate_document,
    validate_voice,
)
from tests import samples


@pytest.fixture
def write(tmp_path):
    def _write(name: str, data: bytes):
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return _write


# --- common checks ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["empty.pdf", "empty.png", "empty.jpg"])
def test_empty_file_is_rejected_whatever_its_extension(write, name):
    assert validate_document(write(name, b"")) is Rejection.EMPTY


def test_file_over_the_byte_limit_is_rejected(write, monkeypatch):
    monkeypatch.setattr(limits, "MAX_UPLOAD_BYTES", 1000)
    assert validate_document(write("big.pdf", b"%PDF-" + b"\0" * 1000)) is Rejection.TOO_LARGE  # size before content
    assert isinstance(validate_document(write("small.png", samples.png_bytes((64, 64)))), ValidatedDocument)


def test_declared_size_is_checked_before_download():
    assert exceeds_upload_limit(limits.MAX_UPLOAD_BYTES + 1)
    assert not exceeds_upload_limit(limits.MAX_UPLOAD_BYTES)
    assert not exceeds_upload_limit(None)  # Telegram did not say: the post-download check decides


def test_missing_file_is_an_environment_failure_not_a_rejection(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_document(tmp_path / "gone.pdf")


def test_voice_is_checked_for_emptiness_and_size_only(write, monkeypatch):
    assert validate_voice(write("v.ogg", b"OggS-whatever")) is None
    assert validate_voice(write("e.ogg", b"")) is Rejection.EMPTY
    monkeypatch.setattr(limits, "MAX_UPLOAD_BYTES", 4)
    assert validate_voice(write("l.ogg", b"12345")) is Rejection.TOO_LARGE


# --- extension is not trusted -------------------------------------------------------------------------------


def test_text_renamed_to_pdf_is_rejected(write):
    assert validate_document(write("fake.pdf", b"This is just a text file.")) is Rejection.UNSUPPORTED_TYPE


@pytest.mark.parametrize("data", [b"GIF89a" + b"\0" * 64, b"BM" + b"\0" * 64, b"PK\x03\x04" + b"\0" * 64])
def test_other_formats_are_unsupported_even_with_an_image_extension(write, data):
    assert validate_document(write("scan.png", data)) is Rejection.UNSUPPORTED_TYPE


def test_the_type_comes_from_the_content_not_from_the_name(write):
    image = validate_document(write("really-an-image.pdf", samples.png_bytes()))
    pdf = validate_document(write("really-a-pdf.png", samples.text_pdf(2)))

    assert isinstance(image, ValidatedDocument) and image.kind == "image"
    assert isinstance(pdf, ValidatedDocument) and pdf.kind == "pdf" and pdf.page_count == 2


# --- PDFs ---------------------------------------------------------------------------------------------------


def test_valid_small_pdf_is_accepted(write):
    path = write("ok.pdf", samples.text_pdf(3))
    assert validate_document(path) == ValidatedDocument(path=path, kind="pdf", page_count=3)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"%PDF-1.4\nthis is not a real pdf body", id="header-then-junk"),
        pytest.param(samples.text_pdf(2)[: len(samples.text_pdf(2)) // 2], id="truncated"),
    ],
)
def test_corrupt_pdf_is_rejected(write, data):
    assert validate_document(write("corrupt.pdf", data)) is Rejection.CORRUPT_PDF


def test_pdf_header_may_follow_a_little_leading_junk(write):
    assert isinstance(validate_document(write("junk-first.pdf", b"\r\n" + samples.text_pdf(1))), ValidatedDocument)


def test_encrypted_pdf_is_rejected_explicitly(write):
    assert validate_document(write("locked.pdf", samples.encrypted_pdf())) is Rejection.ENCRYPTED_PDF


def test_pdf_page_count_boundary(write):
    at_limit = validate_document(write("at.pdf", samples.blank_pdf(limits.MAX_PDF_PAGES)))
    over = validate_document(write("over.pdf", samples.blank_pdf(limits.MAX_PDF_PAGES + 1)))

    assert isinstance(at_limit, ValidatedDocument) and at_limit.page_count == limits.MAX_PDF_PAGES
    assert over is Rejection.TOO_MANY_PAGES


@pytest.mark.parametrize(
    "width, height, user_unit",
    [
        pytest.param(200_000, 200_000, None, id="giant-mediabox"),
        pytest.param(612, 14_400 * 10, None, id="one-giant-side"),
        pytest.param(612, 792, 100_000, id="giant-user-unit"),
        pytest.param(10, 10, None, id="too-small-to-be-a-page"),
        pytest.param(0, 0, None, id="zero-size"),
    ],
)
def test_unreasonable_page_dimensions_are_rejected(write, width, height, user_unit):
    path = write("hostile.pdf", samples.pdf_with_page_box(width, height, user_unit))
    assert validate_document(path) is Rejection.PAGE_SIZE


@pytest.mark.parametrize("width, height", [(595, 842), (612, 792), (842, 1191), (2384, 3370), (227, 850)])
def test_real_world_page_sizes_are_accepted(write, width, height):  # A4, Letter, A3, A0, a till receipt
    assert isinstance(validate_document(write("ok.pdf", samples.pdf_with_page_box(width, height))), ValidatedDocument)


def test_one_hostile_page_among_good_ones_rejects_the_file(write):
    """Every accepted page is measured, not just the first: page 20 is as unreasonable as page 1."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=200_000, height=200_000)
    buf = io.BytesIO()
    writer.write(buf)

    assert validate_document(write("mixed.pdf", buf.getvalue())) is Rejection.PAGE_SIZE


def test_validation_logs_failure_classes_never_file_content(write, caplog):
    caplog.set_level(logging.DEBUG)
    secret = b"%PDF-1.4\nSENTINEL-CONTRACT-TEXT 7700123456 is not a valid pdf body"
    garbled_png = samples.png_bytes()[:60] + b"SENTINEL-CONTRACT-TEXT"

    assert validate_document(write("a.pdf", secret)) is Rejection.CORRUPT_PDF
    assert validate_document(write("b.png", garbled_png)) is Rejection.UNREADABLE_IMAGE

    assert "SENTINEL" not in caplog.text and "7700123456" not in caplog.text


# --- images -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt, name", [("PNG", "a.png"), ("JPEG", "a.jpg"), ("WEBP", "a.webp")])
def test_supported_images_are_accepted_and_reencoded(write, fmt, name):
    path = write(name, samples.image_bytes(fmt))
    result = validate_document(path)

    assert isinstance(result, ValidatedDocument) and result.kind == "image" and result.page_count == 1
    assert result.image_jpeg is not None and result.image_jpeg != path.read_bytes()


def test_undecodable_image_is_rejected(write):
    png = samples.png_bytes()
    assert validate_document(write("cut.png", png[: len(png) // 2])) is Rejection.UNREADABLE_IMAGE


def test_image_too_small_to_hold_a_document_is_rejected(write):
    side = limits.MIN_IMAGE_SIDE_PX - 1
    assert validate_document(write("dot.png", samples.png_bytes((side, side)))) is Rejection.UNREADABLE_IMAGE


def test_image_over_the_pixel_cap_is_rejected_without_decoding_it(write, monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 10_000)
    assert validate_document(write("big.png", samples.png_bytes((101, 100)))) is Rejection.IMAGE_SIZE
    assert isinstance(validate_document(write("ok.png", samples.png_bytes((100, 100)))), ValidatedDocument)


def test_decompression_bomb_image_is_rejected(write):
    """45 bytes that declare 3.6 billion pixels: refused from the header, nothing is allocated."""
    bomb = samples.png_declaring(60_000, 60_000)
    assert len(bomb) < 100
    assert validate_document(write("bomb.png", bomb)) is Rejection.IMAGE_SIZE


def decoded(jpeg: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    return img


def test_normalized_image_is_a_bounded_metadata_free_jpeg_with_the_orientation_applied():
    source = Image.new("RGB", (3000, 2000), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise on display
    exif[0x010F] = "SecretCameraMaker"
    raw = io.BytesIO()
    source.save(raw, format="JPEG", exif=exif, icc_profile=b"not-really-an-icc-profile")

    with Image.open(io.BytesIO(raw.getvalue())) as img:
        img.load()
        out = decoded(normalize_image(img))

    assert out.format == "JPEG" and out.mode == "RGB"
    assert max(out.size) == limits.MAX_IMAGE_SIDE_PX
    assert out.height > out.width  # 3000x2000 landscape became portrait: the EXIF rotation was applied
    assert not out.getexif() and "icc_profile" not in out.info and b"SecretCameraMaker" not in normalize_image(source)


def test_a_large_jpeg_is_downscaled_to_the_cap_keeping_its_aspect_ratio(write, monkeypatch):
    """The JPEG is decoded at reduced scale (draft mode), which must not change the final size or proportions."""
    monkeypatch.setattr(limits, "MAX_IMAGE_SIDE_PX", 100)
    result = validate_document(write("big.jpg", samples.jpeg_bytes((800, 600))))
    assert decoded(result.image_jpeg).size == (100, 75)


def test_exif_rotation_and_reduced_scale_decoding_work_together(write, monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMAGE_SIDE_PX", 100)
    exif = Image.Exif()
    exif[0x0112] = 6
    raw = io.BytesIO()
    drawn = samples.drawn_image((800, 600))
    drawn.save(raw, format="JPEG", exif=exif)

    result = validate_document(write("rotated.jpg", raw.getvalue()))

    assert decoded(result.image_jpeg).size == (75, 100)  # 800x600 landscape -> portrait, then capped at 100


def test_small_images_are_not_upscaled(write):
    result = validate_document(write("small.png", samples.png_bytes((200, 100))))
    assert decoded(result.image_jpeg).size == (200, 100)


@pytest.mark.parametrize(
    "mode, fmt",
    [("1", "PNG"), ("L", "PNG"), ("L", "JPEG"), ("LA", "PNG"), ("P", "PNG"), ("RGBA", "PNG"), ("RGBA", "WEBP"), ("CMYK", "JPEG")],
)
def test_every_common_pixel_mode_normalizes_to_an_rgb_jpeg(write, mode, fmt):
    buf = io.BytesIO()
    drawn = samples.drawn_image((200, 120))
    (drawn.convert("RGB").convert("P") if mode == "P" else drawn.convert(mode)).save(buf, format=fmt)

    result = validate_document(write(f"{mode}.{fmt.lower()}", buf.getvalue()))

    assert isinstance(result, ValidatedDocument), result
    out = decoded(result.image_jpeg)
    assert out.mode == "RGB" and out.size == (200, 120)


def test_transparency_is_flattened_onto_white_not_black():
    rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # fully transparent
    assert decoded(normalize_image(rgba)).getpixel((10, 10)) == (255, 255, 255)


def test_sixteen_bit_grayscale_scan_is_not_turned_into_a_white_page():
    scan = Image.new("I;16", (64, 64), 20_000)  # mid-gray; a plain RGB conversion would saturate to white
    assert decoded(normalize_image(scan)).getpixel((10, 10))[0] < 120


def test_a_validated_image_is_decoded_not_copied(write):
    """What goes downstream is the re-encoded JPEG: no byte of the uploaded file is forwarded."""
    path = write("a.png", samples.png_bytes())
    result = validate_document(path)
    assert result.image_jpeg.startswith(b"\xff\xd8\xff") and not result.image_jpeg.startswith(b"\x89PNG")
