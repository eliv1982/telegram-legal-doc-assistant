"""
Coverage policy (Stage 3): text layer first and page by page, Vision only for pages without text, an explicit
page budget, and a result that says exactly which pages were analysed. Offline: Vision is a recording fake and,
unless a test says otherwise, so is the Poppler renderer.
"""
import asyncio
import shutil
import threading

import pytest

from services import document_extraction as extraction
from services import limits, pdf_converter
from services.document_extraction import (
    DocumentExtractionResult,
    coverage_summary,
    extract_document,
    format_coverage,
    format_page_ranges,
)
from services.pdf_converter import RenderedPage, RenderError
from services.validation import Rejection, ValidatedDocument, validate_document
from tests import samples


class RecordingOcr:
    """Async stand-in for OpenAIService.extract_text_from_image. Every call is a paid Vision call."""

    def __init__(self, reply=None):
        self.images: list[bytes] = []
        self.mimes: list[str] = []
        self._reply = reply

    async def __call__(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        self.images.append(image_bytes)
        self.mimes.append(mime_type)
        if self._reply is not None:
            return self._reply(image_bytes)
        return f"VISION<{image_bytes.decode()}>"


class FakeRenderer:
    """Replaces pdf_converter.render_pages: page N renders to the bytes b'page-N' (so Vision output names the page)."""

    def __init__(self, blank_pages=(), error: Rejection | None = None):
        self.requests: list[list[int]] = []
        self._blank = set(blank_pages)
        self._error = error

    def __call__(self, path, pages):
        self.requests.append(list(pages))
        if self._error is not None:
            raise RenderError(self._error)
        return {n: RenderedPage(jpeg=f"page-{n}".encode(), blank=n in self._blank) for n in pages}


@pytest.fixture
def renderer(monkeypatch):
    fake = FakeRenderer()
    monkeypatch.setattr(pdf_converter, "render_pages", fake)
    return fake


@pytest.fixture
def validated(tmp_path):
    def _validated(data: bytes, name: str = "doc.pdf") -> ValidatedDocument:
        path = tmp_path / name
        path.write_bytes(data)
        document = validate_document(path)
        assert isinstance(document, ValidatedDocument), document
        return document

    return _validated


# --- born-digital PDFs: text first, no rendering, no Vision ------------------------------------------------------


async def test_a_one_page_text_pdf_is_read_from_its_text_layer(validated, renderer):
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.text_pdf(1)), ocr)

    assert isinstance(result, DocumentExtractionResult)
    assert (result.total_pages, result.analysed_pages, result.skipped_pages) == (1, (1,), ())
    assert result.extraction_methods == (extraction.METHOD_TEXT_LAYER,)
    assert "CLAUSE-1" in result.extracted_text
    assert renderer.requests == [] and ocr.images == []  # nothing rendered, nothing paid for
    assert not result.truncated and result.is_complete


async def test_a_multi_page_text_pdf_within_budget_keeps_page_boundaries_in_order(validated, renderer):
    result = await extract_document(validated(samples.text_pdf(3)), RecordingOcr())

    assert result.analysed_pages == (1, 2, 3)
    text = result.extracted_text
    assert text.index("[Страница 1]") < text.index("CLAUSE-1") < text.index("[Страница 2]") < text.index("CLAUSE-2")
    assert text.index("CLAUSE-2") < text.index("[Страница 3]") < text.index("CLAUSE-3")


async def test_a_pdf_over_the_page_budget_is_truncated_and_says_so(validated, renderer):
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.text_pdf(12)), ocr)

    assert result.total_pages == 12
    assert result.analysed_pages == (1, 2, 3, 4, 5)
    assert result.skipped_pages == (6, 7, 8, 9, 10, 11, 12)
    assert result.truncated and not result.is_complete
    assert set(result.analysed_pages).isdisjoint(result.skipped_pages)
    assert renderer.requests == [] and ocr.images == []  # skipped pages cost nothing


async def test_the_last_page_inside_the_budget_reaches_the_analysis_input_and_later_ones_do_not(validated, renderer):
    result = await extract_document(validated(samples.text_pdf(12)), RecordingOcr())

    assert "CLAUSE-5" in result.extracted_text and "CLAUSE-5" in result.analysis_text
    assert "CLAUSE-6" not in result.analysis_text and "[Страница 6]" not in result.analysis_text


async def test_a_pdf_exactly_at_the_budget_is_complete(validated, renderer):
    result = await extract_document(validated(samples.text_pdf(limits.MAX_ANALYSED_PAGES)), RecordingOcr())
    assert result.skipped_pages == () and result.is_complete


async def test_the_budget_is_a_limit_not_a_constant(validated, renderer, monkeypatch):
    monkeypatch.setattr(limits, "MAX_ANALYSED_PAGES", 2)
    result = await extract_document(validated(samples.text_pdf(4)), RecordingOcr())
    assert (result.analysed_pages, result.skipped_pages) == ((1, 2), (3, 4))


# --- scanned pages: bounded Vision fallback ------------------------------------------------------------------------


async def test_a_page_without_a_text_layer_goes_to_vision_and_text_pages_do_not(validated, renderer):
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.pdf_bytes([samples.page_text(1), None, samples.page_text(3)])), ocr)

    assert renderer.requests == [[2]]  # only the scan is rendered
    assert ocr.images == [b"page-2"] and ocr.mimes == ["image/jpeg"]
    assert result.extraction_methods == (extraction.METHOD_TEXT_LAYER, extraction.METHOD_VISION)
    assert result.analysed_pages == (1, 2, 3)
    text = result.extracted_text
    assert text.index("CLAUSE-1") < text.index("VISION<page-2>") < text.index("CLAUSE-3")  # page order is preserved
    assert text.index("[Страница 2]") < text.index("VISION<page-2>") < text.index("[Страница 3]")


async def test_an_all_scanned_pdf_is_read_by_vision_page_by_page(validated, renderer):
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.pdf_bytes([None, None, None])), ocr)

    assert renderer.requests == [[1, 2, 3]]
    assert ocr.images == [b"page-1", b"page-2", b"page-3"]
    assert result.extraction_methods == (extraction.METHOD_VISION,)
    assert result.analysed_pages == (1, 2, 3)


async def test_vision_is_bounded_by_the_same_page_budget(validated, renderer):
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.pdf_bytes([None] * 8)), ocr)

    assert renderer.requests == [[1, 2, 3, 4, 5]]  # pages 6-8 are never rendered
    assert len(ocr.images) == limits.MAX_ANALYSED_PAGES
    assert result.analysed_pages == (1, 2, 3, 4, 5) and result.skipped_pages == (6, 7, 8)


async def test_a_blank_scanned_page_costs_no_vision_call_and_is_not_counted_as_analysed(validated, monkeypatch):
    monkeypatch.setattr(pdf_converter, "render_pages", FakeRenderer(blank_pages={2}))
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.pdf_bytes([None, None, None])), ocr)

    assert ocr.images == [b"page-1", b"page-3"]
    assert result.analysed_pages == (1, 3) and result.unreadable_pages == (2,)
    assert "(страницы: 2)" in coverage_summary(result)


async def test_vision_finding_no_text_marks_the_page_unreadable_not_analysed(validated, renderer):
    ocr = RecordingOcr(reply=lambda image: extraction.UNREADABLE_MARKER if image == b"page-2" else "readable text")

    result = await extract_document(validated(samples.pdf_bytes([None, None])), ocr)

    assert result.analysed_pages == (1,) and result.unreadable_pages == (2,)
    assert "[Страница 2]" not in result.extracted_text


async def test_a_short_text_layer_is_kept_when_vision_finds_nothing_better(validated, renderer):
    ocr = RecordingOcr(reply=lambda image: extraction.UNREADABLE_MARKER)

    result = await extract_document(validated(samples.pdf_bytes(["Signed: I. Ivanov"])), ocr)  # < MIN_PAGE_TEXT_CHARS

    assert renderer.requests == [[1]] and len(ocr.images) == 1  # it was treated as a scan ...
    assert result.analysed_pages == (1,) and "Signed: I. Ivanov" in result.extracted_text  # ... without losing its text
    assert result.extraction_methods == (extraction.METHOD_TEXT_LAYER,)


async def test_a_document_with_no_readable_page_is_rejected_without_a_vision_call(validated, monkeypatch):
    monkeypatch.setattr(pdf_converter, "render_pages", FakeRenderer(blank_pages={1, 2}))
    ocr = RecordingOcr()

    assert await extract_document(validated(samples.pdf_bytes([None, None])), ocr) is Rejection.NO_TEXT
    assert ocr.images == []


async def test_vision_marker_on_every_page_is_rejected_as_no_text(validated, renderer):
    ocr = RecordingOcr(reply=lambda image: f"  {extraction.UNREADABLE_MARKER}  ")
    assert await extract_document(validated(samples.pdf_bytes([None])), ocr) is Rejection.NO_TEXT


def test_the_unreadable_marker_is_the_one_the_ocr_prompt_asks_for():
    from prompts.analysis_prompt import DOCUMENT_OCR_PROMPT

    assert extraction.UNREADABLE_MARKER in DOCUMENT_OCR_PROMPT


# --- ordering: free local work completes before any paid call ----------------------------------------------------


@pytest.mark.parametrize("reason", [Rejection.RENDER_FAILED, Rejection.RENDER_TIMEOUT])
async def test_a_render_failure_happens_before_any_vision_call_even_for_earlier_scanned_pages(
    validated, monkeypatch, reason
):
    monkeypatch.setattr(pdf_converter, "render_pages", FakeRenderer(error=reason))
    ocr = RecordingOcr()

    result = await extract_document(validated(samples.pdf_bytes([samples.page_text(1), None, None])), ocr)

    assert result is reason
    assert ocr.images == []  # pages 1 (text) and 2-3 (scans) were all prepared before paying for anything


async def test_an_ocr_failure_is_a_transient_error_and_is_not_swallowed(validated, renderer):
    async def openai_down(image_bytes, mime_type):
        raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError):
        await extract_document(validated(samples.pdf_bytes([None])), openai_down)


# --- images --------------------------------------------------------------------------------------------------------


async def test_an_image_is_one_unit_read_by_vision_from_the_normalized_jpeg(validated):
    document = validated(samples.png_bytes(), "photo.png")
    ocr = RecordingOcr(reply=lambda image: "Text of the photographed contract")

    result = await extract_document(document, ocr)

    assert ocr.images == [document.image_jpeg] and ocr.images[0].startswith(b"\xff\xd8\xff")  # never the raw upload
    assert ocr.mimes == ["image/jpeg"]
    assert result.is_image and (result.total_pages, result.analysed_pages) == (1, (1,))
    assert result.extraction_methods == (extraction.METHOD_VISION,)
    assert "[Страница" not in result.extracted_text  # a single image has no page boundaries to mark


@pytest.mark.parametrize("reply", ["", "   \n", extraction.UNREADABLE_MARKER])
async def test_an_image_with_no_readable_text_is_rejected(validated, reply):
    assert await extract_document(validated(samples.png_bytes(), "p.png"), RecordingOcr(reply=lambda i: reply)) is (
        Rejection.NO_TEXT
    )


# --- text budget ---------------------------------------------------------------------------------------------------


async def test_the_character_budget_holds_the_whole_page_budget_so_no_counted_page_is_cut_later(
    validated, monkeypatch
):
    """MAX_ANALYSED_PAGES pages of the largest allowed size must still fit under the analysis input limit."""
    huge = {n: "x" * (limits.MAX_PAGE_CHARS * 3) for n in range(1, limits.MAX_ANALYSED_PAGES + 1)}
    monkeypatch.setattr(pdf_converter, "extract_page_texts", lambda path, pages: {n: huge[n] for n in pages})

    result = await extract_document(validated(samples.text_pdf(8)), RecordingOcr())

    assert result.analysed_pages == tuple(range(1, limits.MAX_ANALYSED_PAGES + 1))  # none dropped for length
    assert result.clipped_pages == result.analysed_pages  # each was cut to MAX_PAGE_CHARS, and says so
    assert len(result.extracted_text) <= limits.ANALYSIS_CHAR_LIMIT
    assert "Текст сокращён по лимиту длины (страницы: 1–5)" in coverage_summary(result)


async def test_a_clipped_image_text_is_disclosed(validated):
    ocr = RecordingOcr(reply=lambda image: "w" * (limits.MAX_PAGE_CHARS + 1))
    result = await extract_document(validated(samples.png_bytes(), "p.png"), ocr)
    assert result.clipped_pages == (1,) and "сокращён" in coverage_summary(result)


# --- the disclosure text -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pages, expected",
    [((), ""), ((1,), "1"), ((1, 2, 3), "1–3"), ((1, 2, 3, 5), "1–3, 5"), ((2, 4, 6), "2, 4, 6"), ((5, 4, 3), "3–5")],
)
def test_page_ranges_are_compact(pages, expected):
    assert format_page_ranges(pages) == expected


def result_of(**overrides) -> DocumentExtractionResult:
    fields = dict(
        extracted_text="text", total_pages=12, analysed_pages=(1, 2, 3, 4, 5), skipped_pages=(6, 7, 8, 9, 10, 11, 12),
        unreadable_pages=(), clipped_pages=(), extraction_methods=("text_layer",), is_image=False,
    )
    return DocumentExtractionResult(**{**fields, **overrides})


def test_a_truncated_pdf_discloses_what_was_and_was_not_analysed():
    assert format_coverage(result_of()) == (
        "ℹ️ Проанализированы только страницы 1–5 из 12. Страницы 6–12 в анализ не вошли."
    )


def test_a_complete_pdf_discloses_the_pages_analysed():
    complete = result_of(total_pages=3, analysed_pages=(1, 2, 3), skipped_pages=())
    assert format_coverage(complete) == "ℹ️ Проанализированные страницы: 1–3 из 3."
    one = result_of(total_pages=1, analysed_pages=(1,), skipped_pages=())
    assert format_coverage(one) == "ℹ️ Проанализированные страницы: 1 из 1."


def test_an_image_discloses_the_uploaded_image():
    image = result_of(total_pages=1, analysed_pages=(1,), skipped_pages=(), is_image=True)
    assert format_coverage(image) == "ℹ️ Проанализировано: загруженное изображение."


def test_unreadable_pages_are_listed_separately_from_analysed_ones():
    partial = result_of(total_pages=4, analysed_pages=(1, 3, 4), skipped_pages=(), unreadable_pages=(2,))
    summary = coverage_summary(partial)
    assert summary == "Проанализированные страницы: 1, 3–4 из 4. Текст не распознан (страницы: 2)."


def test_the_model_is_told_when_it_saw_only_part_of_the_document_and_not_otherwise():
    partial = result_of()
    assert partial.analysis_text.endswith("\n\ntext")
    assert "1–5 из 12" in partial.analysis_text and "Не делай выводов об отсутствии" in partial.analysis_text

    complete = result_of(total_pages=2, analysed_pages=(1, 2), skipped_pages=())
    assert complete.analysis_text == "text"  # a complete document is passed through untouched


# --- async / resource safety ---------------------------------------------------------------------------------------


async def test_pdf_work_runs_off_the_event_loop_thread(validated, monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_text = pdf_converter.extract_page_texts

    def spy_text(path, pages):
        seen["text"] = threading.get_ident()
        return real_text(path, pages)

    def spy_render(path, pages):
        seen["render"] = threading.get_ident()
        return {n: RenderedPage(jpeg=b"page", blank=False) for n in pages}

    monkeypatch.setattr(pdf_converter, "extract_page_texts", spy_text)
    monkeypatch.setattr(pdf_converter, "render_pages", spy_render)

    await extract_document(validated(samples.pdf_bytes([samples.page_text(1), None])), RecordingOcr())

    assert set(seen) == {"text", "render"}
    assert all(thread != loop_thread for thread in seen.values())


async def test_a_blocked_renderer_does_not_block_the_event_loop(validated, monkeypatch):
    """
    The renderer parks its worker thread until the *event loop* releases it. If rendering ran on the loop thread,
    nothing could release it and the 10 s guard would fail the test; offloaded, it passes at once.
    """
    entered, release = threading.Event(), threading.Event()

    def parked_renderer(path, pages):
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("the event loop was blocked while rendering")
        return {n: RenderedPage(jpeg=b"page", blank=False) for n in pages}

    monkeypatch.setattr(pdf_converter, "render_pages", parked_renderer)
    task = asyncio.create_task(extract_document(validated(samples.pdf_bytes([None])), RecordingOcr()))

    assert await asyncio.to_thread(entered.wait, 10)  # the loop is free: it can run this while rendering is parked
    assert not task.done()
    release.set()
    assert isinstance(await task, DocumentExtractionResult)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler (pdftoppm) is not installed")
async def test_scanned_pages_are_really_rendered_for_vision_with_poppler(validated):
    ocr = RecordingOcr(reply=lambda image: "recognised")

    result = await extract_document(validated(samples.pdf_bytes([samples.page_text(1), None])), ocr)

    assert result.analysed_pages == (1, 2) and len(ocr.images) == 1
    assert ocr.images[0].startswith(b"\xff\xd8\xff")
