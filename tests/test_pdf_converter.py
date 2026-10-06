"""
services/pdf_converter.py against a tiny generated PDF. Only the page-to-image test needs Poppler.
"""
import shutil

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from services.pdf_converter import extract_text_from_pdf, pdf_first_page_to_image


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 750, "Contract number 15 of 2026")
    pdf.save()
    return path


def test_extract_text_from_pdf_reads_the_text_layer(sample_pdf):
    assert "Contract number 15" in extract_text_from_pdf(sample_pdf)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler (pdftoppm) is not installed")
def test_pdf_first_page_to_image_returns_jpeg_bytes(sample_pdf):
    assert pdf_first_page_to_image(sample_pdf).startswith(b"\xff\xd8\xff")
