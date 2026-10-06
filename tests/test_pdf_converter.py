"""
services/pdf_converter.py: page text and bounded Poppler rendering. Tests that need the real pdftoppm are skipped
without it (CI installs Poppler); the error-mapping and timeout tests replace pdf2image and need nothing.
"""
import io
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError

from services import limits, pdf_converter
from services.pdf_converter import RenderError, extract_page_texts, render_pages
from services.validation import Rejection
from tests import samples

needs_poppler = pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler (pdftoppm) is not installed")


@pytest.fixture
def pdf_file(tmp_path):
    def _make(data: bytes, name: str = "doc.pdf"):
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return _make


def size_of(jpeg: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(jpeg)).size


# --- text layer ---------------------------------------------------------------------------------------------


def test_extract_page_texts_reads_the_text_layer_page_by_page(pdf_file):
    path = pdf_file(samples.text_pdf(3))

    texts = extract_page_texts(path, [1, 3])

    assert set(texts) == {1, 3}
    assert "CLAUSE-1" in texts[1] and "CLAUSE-3" in texts[3]
    assert "CLAUSE-3" not in texts[1]  # pages are not mixed


def test_a_scanned_page_has_an_empty_text_layer(pdf_file):
    assert extract_page_texts(pdf_file(samples.pdf_bytes([samples.page_text(1), None])), [1, 2])[2] == ""


def test_a_page_whose_extraction_fails_does_not_stop_the_others(monkeypatch):
    class Page:
        def __init__(self, text=None, fails=False):
            self._text, self._fails = text, fails

        def extract_text(self):
            if self._fails:
                raise ValueError("broken content stream")
            return self._text

    pages = [Page("first"), Page(fails=True), Page("lone surrogate \ud835 and null \x00 inside")]
    monkeypatch.setattr(pdf_converter, "PdfReader", lambda path: SimpleNamespace(pages=pages))

    texts = extract_page_texts("ignored.pdf", [1, 2, 3])

    assert texts[1] == "first" and texts[2] == ""
    assert texts[3] == "lone surrogate  and null  inside"  # the text can be JSON/UTF-8 encoded for the API
    texts[3].encode("utf-8")


# --- real Poppler ------------------------------------------------------------------------------------------


@needs_poppler
def test_render_returns_a_bounded_jpeg(pdf_file):
    page = render_pages(pdf_file(samples.text_pdf(1)), [1])[1]

    assert page.jpeg.startswith(b"\xff\xd8\xff")
    assert page.blank is False
    assert max(size_of(page.jpeg)) == limits.RENDER_LONG_SIDE_PX  # the long side is pinned, not DPI-derived


@needs_poppler
def test_render_only_the_requested_pages(pdf_file):
    assert set(render_pages(pdf_file(samples.pdf_bytes([None, None, None])), [1, 3])) == {1, 3}
    assert render_pages(pdf_file(samples.text_pdf(1)), []) == {}


@needs_poppler
def test_blank_page_is_flagged_so_no_vision_call_is_spent_on_it(pdf_file):
    assert render_pages(pdf_file(samples.blank_pdf()), [1])[1].blank is True


@needs_poppler
def test_even_the_largest_page_the_pdf_spec_allows_renders_into_a_bounded_bitmap(pdf_file):
    """
    Defence in depth: preflight rejects pages this big, but if one ever reached Poppler, `-scale-to` still caps
    the bitmap at RENDER_LONG_SIDE_PX squared (14400 pt at 200 DPI would be a 40 000 px wide image). The page is the
    spec maximum, not the audit's 200000 pt, so that even a regression here would not allocate absurd amounts.
    """
    page = render_pages(pdf_file(samples.pdf_with_page_box(14_400, 14_400)), [1])[1]
    assert max(size_of(page.jpeg)) <= limits.RENDER_LONG_SIDE_PX


@needs_poppler
def test_a_hairline_render_is_a_failure_not_a_page(pdf_file):
    """3600x36 pt passes the size limits but renders as a ~2048x21 px strip: Poppler's output is validated too."""
    with pytest.raises(RenderError) as caught:
        render_pages(pdf_file(samples.pdf_with_page_box(3600, 36)), [1])
    assert caught.value.reason is Rejection.RENDER_FAILED


@needs_poppler
def test_poppler_cannot_parse_the_file_is_a_render_failure(pdf_file):
    with pytest.raises(RenderError) as caught:
        render_pages(pdf_file(b"%PDF-1.4\nnot really a pdf"), [1])
    assert caught.value.reason is Rejection.RENDER_FAILED


# --- timeouts and error mapping (pdf2image replaced; no Poppler needed) ------------------------------------


@pytest.fixture
def poppler_calls(monkeypatch):
    """Replaces both pdf2image entry points and records their keyword arguments."""
    calls = SimpleNamespace(info=[], render=[], render_result=lambda: [Image.new("RGB", (800, 1100), "white")])

    def fake_info(path, **kwargs):
        calls.info.append(kwargs)
        return {"Pages": 1}

    def fake_render(path, **kwargs):
        calls.render.append(kwargs)
        return calls.render_result()

    monkeypatch.setattr(pdf_converter, "pdfinfo_from_path", fake_info)
    monkeypatch.setattr(pdf_converter, "convert_from_path", fake_render)
    return calls


def test_every_poppler_call_has_a_timeout_and_a_bounded_size(poppler_calls):
    render_pages("doc.pdf", [1, 2])

    # convert_from_path runs an unbounded pdfinfo of its own, so the bounded one is made first, here.
    assert poppler_calls.info == [{"timeout": limits.RENDER_TIMEOUT_SECONDS}]
    assert [c["timeout"] for c in poppler_calls.render] == [limits.RENDER_TIMEOUT_SECONDS] * 2
    assert [c["size"] for c in poppler_calls.render] == [limits.RENDER_LONG_SIDE_PX] * 2
    assert [(c["first_page"], c["last_page"]) for c in poppler_calls.render] == [(1, 1), (2, 2)]  # one page per call


def test_the_timeout_comes_from_the_limits_module(poppler_calls, monkeypatch):
    monkeypatch.setattr(limits, "RENDER_TIMEOUT_SECONDS", 3)
    render_pages("doc.pdf", [1])
    assert poppler_calls.render[0]["timeout"] == 3 and poppler_calls.info[0]["timeout"] == 3


def test_a_render_timeout_is_categorised_as_a_timeout(poppler_calls):
    def hang(path, **kwargs):
        raise PDFPopplerTimeoutError("Run poppler timeout.")

    poppler_calls.render_result = lambda: hang("x")
    with pytest.raises(RenderError) as caught:
        render_pages("doc.pdf", [1])
    assert caught.value.reason is Rejection.RENDER_TIMEOUT


def test_a_pdfinfo_timeout_is_categorised_as_a_timeout_and_nothing_is_rendered(poppler_calls, monkeypatch):
    def hang(path, **kwargs):
        raise PDFPopplerTimeoutError("Run poppler poppler timeout.")

    monkeypatch.setattr(pdf_converter, "pdfinfo_from_path", hang)
    with pytest.raises(RenderError) as caught:
        render_pages("doc.pdf", [1])
    assert caught.value.reason is Rejection.RENDER_TIMEOUT
    assert poppler_calls.render == []


def test_a_timeout_on_one_page_abandons_the_document(poppler_calls):
    outcomes = iter([[Image.new("RGB", (800, 1100), "white")], PDFPopplerTimeoutError("timeout")])

    def next_outcome():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    poppler_calls.render_result = next_outcome
    with pytest.raises(RenderError) as caught:
        render_pages("doc.pdf", [1, 2, 3])
    assert caught.value.reason is Rejection.RENDER_TIMEOUT
    assert len(poppler_calls.render) == 2  # page 3 was never attempted


def test_poppler_not_being_able_to_read_the_file_is_a_render_failure(poppler_calls):
    def unreadable():
        raise PDFPageCountError("Unable to get page count.")

    poppler_calls.render_result = unreadable
    with pytest.raises(RenderError) as caught:
        render_pages("doc.pdf", [1])
    assert caught.value.reason is Rejection.RENDER_FAILED


@pytest.mark.parametrize(
    "result",
    [pytest.param(lambda: [], id="no-image-at-all"), pytest.param(lambda: [Image.new("RGB", (1, 1), "white")], id="1x1")],
)
def test_empty_or_tiny_render_output_is_a_render_failure(poppler_calls, result):
    poppler_calls.render_result = result
    with pytest.raises(RenderError) as caught:
        render_pages("doc.pdf", [1])
    assert caught.value.reason is Rejection.RENDER_FAILED


def test_missing_poppler_is_not_blamed_on_the_file(poppler_calls, monkeypatch):
    """An environment problem must reach the generic error path, not be reported as a bad document."""

    def not_installed(path, **kwargs):
        raise PDFInfoNotInstalledError("Unable to get page count. Is poppler installed and in PATH?")

    monkeypatch.setattr(pdf_converter, "pdfinfo_from_path", not_installed)
    with pytest.raises(PDFInfoNotInstalledError):
        render_pages("doc.pdf", [1])
