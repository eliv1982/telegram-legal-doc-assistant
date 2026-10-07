"""
Regenerates the synthetic demo in docs/demo/ (run from the repository root):

    uv run python -m docs.demo.generate_demo

Everything here is fictitious: the company names, the contract, and the requisites (the INNs use region code 00 and
the OGRN starts with 0, neither of which can belong to a real entity; they are only valid by checksum).

What is REAL: the sample PDF goes through the repository's own validation, text extraction, deterministic requisite
checks, evidence verification, report composition and checklist rendering.
What is SCRIPTED: the model's output (ISSUES, REPORT below). No OpenAI or Telegram call is made, and the module does
not import `config`, so no `.env` is read.

tests/test_demo_assets.py regenerates the demo and fails if the committed files are stale.
"""
import asyncio
import io
import tempfile
from pathlib import Path

from PIL import ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from services.checklist_generator import FONT_BOLD, FONT_DIR, FONT_FILES, FONT_REGULAR, generate_checklist
from services.deterministic_checks import CheckStatus, run_deterministic_checks
from services.document_extraction import extract_document, format_coverage
from services.grounding import ground_issues
from services.report import compose_report, compose_tts_script, format_automatic_checks
from services.schemas import ChecklistItem, Issue, ReportResult
from services.validation import Rejection, validate_document

DEMO_DIR = Path(__file__).resolve().parent
# Same value as handlers.document.REPORT_CHAR_LIMIT (that module imports `config`, so it is not imported here);
# tests/test_demo_assets.py checks that they agree.
REPORT_CHAR_LIMIT = 4000

# --- The synthetic document -------------------------------------------------------------------------------------
# The buyer's INN has a deliberate one-digit typo: 0076543215 would be valid, 0076543216 is not.
CONTRACT_PARAGRAPHS = (
    ("title", "ДОГОВОР ПОСТАВКИ № ДЕМО-001"),
    ("note", "Демонстрационный образец. Все названия и реквизиты вымышлены; документ не имеет юридической силы."),
    ("body", "г. Вымышленск, 01.01.2026"),
    (
        "body",
        "ООО Демо-Поставщик (далее — Поставщик) и ООО Демо-Покупатель (далее — Покупатель) заключили "
        "настоящий договор о нижеследующем.",
    ),
    ("heading", "1. Предмет договора"),
    (
        "body",
        "1.1. Поставщик обязуется передать Покупателю офисную бумагу формата А4 в количестве 500 пачек, "
        "а Покупатель — принять и оплатить товар.",
    ),
    ("heading", "2. Цена и порядок расчётов"),
    ("body", "2.1. Общая цена договора составляет 100 000,00 руб., в том числе НДС 20% — 20 000,00 руб."),
    ("body", "2.2. Покупатель оплачивает товар в течение 5 рабочих дней с момента подписания договора."),
    ("heading", "3. Ответственность сторон"),
    (
        "body",
        "3.1. За просрочку оплаты Покупатель уплачивает Поставщику неустойку в размере 1% от суммы долга "
        "за каждый день просрочки.",
    ),
    ("heading", "4. Реквизиты сторон"),
    ("body", "Поставщик: ООО Демо-Поставщик. ИНН 0012345673, КПП 000101001, ОГРН 0123456789016."),
    ("body", "Покупатель: ООО Демо-Покупатель. ИНН 0076543216, КПП 000201001."),
)

# --- The scripted stand-in for the model (NOT real model output) ------------------------------------------------
# The three findings show the three evidence outcomes: a verbatim quote (shown as a verified quotation), a paraphrase
# (kept as a finding, but not shown as a quotation), and no quote at all (a finding about something missing).
ISSUES = [
    Issue(
        priority="high",
        title="Неустойка предусмотрена только для Покупателя",
        description="Ответственность Поставщика за нарушение договора не определена.",
        evidence="За просрочку оплаты Покупатель уплачивает Поставщику неустойку в размере 1% от суммы долга "
                 "за каждый день просрочки.",
    ),
    Issue(
        priority="medium",
        title="Сумма НДС не сходится с ценой договора",
        description="При ставке 20% НДС, включённый в 100 000,00 руб., составляет около 16 666,67 руб., а не 20 000,00 руб.",
        evidence="Цена 100 000 руб. включает НДС 20% в размере 20 000 руб.",  # a paraphrase: not in the document
    ),
    Issue(
        priority="medium",
        title="Не указан срок поставки",
        description="В договоре нет срока, к которому Поставщик должен передать товар.",
        evidence=None,
    ),
]

REPORT = ReportResult(
    text_report=(
        "Договор поставки № ДЕМО-001 между ООО Демо-Поставщик и ООО Демо-Покупатель.\n\n"
        "Резюме. Поставщик передаёт 500 пачек офисной бумаги, цена договора — 100 000,00 руб., оплата в течение "
        "5 рабочих дней с подписания. По оценке модели, условия смещены в пользу Поставщика.\n\n"
        "Замечания (предварительно):\n"
        "• Высокий приоритет. Неустойка предусмотрена только за просрочку оплаты Покупателем; ответственность "
        "Поставщика не определена.\n"
        "• Средний приоритет. Указанная сумма НДС (20 000,00 руб.) не сходится с ценой при ставке 20%: по расчёту "
        "модели получается около 16 666,67 руб.\n"
        "• Средний приоритет. Не указан срок поставки.\n\n"
        "Рекомендации: добавить ответственность Поставщика, уточнить сумму НДС и согласовать срок поставки "
        "до подписания."
    ),
    tts_script=(
        "Это договор поставки офисной бумаги на сто тысяч рублей. Главное замечание: неустойка предусмотрена "
        "только для покупателя, ответственность поставщика не определена. Также, по оценке модели, сумма НДС "
        "не сходится с ценой, и не указан срок поставки. Рекомендую добавить ответственность поставщика, "
        "уточнить НДС и согласовать срок поставки до подписания."
    ),
    checklist_items=[
        ChecklistItem(priority="medium", text="Пересчитать сумму НДС и привести цену договора в соответствие со ставкой 20%."),
        ChecklistItem(priority="high", text="Добавить ответственность Поставщика за просрочку поставки и нарушение условий договора."),
        ChecklistItem(priority="low", text="Уточнить порядок приёмки товара по количеству и качеству."),
        ChecklistItem(priority="medium", text="Согласовать и указать срок поставки."),
    ],
)


def _contract_pdf() -> bytes:
    for name, filename in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
    base = {"fontName": FONT_REGULAR, "fontSize": 11, "leading": 15, "spaceAfter": 6}
    styles = {
        "title": ParagraphStyle("title", **{**base, "fontName": FONT_BOLD, "fontSize": 16, "leading": 20}),
        "note": ParagraphStyle("note", **{**base, "fontSize": 9, "textColor": colors.HexColor("#52606D")}),
        "heading": ParagraphStyle("heading", **{**base, "fontName": FONT_BOLD, "spaceBefore": 8}),
        "body": ParagraphStyle("body", **base),
    }
    story = [Paragraph(text, styles[style]) for style, text in CONTRACT_PARAGRAPHS]
    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
        title="Демонстрационный образец договора", author="", invariant=1,
    ).build(story)
    return buffer.getvalue()


async def _run_pipeline(contract: bytes) -> dict[str, bytes]:
    """The same steps, in the same order, as handlers.document.run_pipeline, minus Telegram and OpenAI."""
    async def no_ocr(image: bytes, mime_type: str) -> str:
        raise AssertionError("the demo contract has a text layer: no page may be sent to the vision model")

    with tempfile.TemporaryDirectory() as workdir:
        path = Path(workdir) / "document.pdf"
        path.write_bytes(contract)
        document = validate_document(path)
        if isinstance(document, Rejection):
            raise RuntimeError(f"the demo contract was rejected: {document.value}")
        extraction = await extract_document(document, no_ocr)
        if isinstance(extraction, Rejection):
            raise RuntimeError(f"no text was extracted from the demo contract: {extraction.value}")

    checks = run_deterministic_checks(extraction.extracted_text, analysed_pages=extraction.analysed_pages)
    findings = ground_issues(ISSUES, extraction.extracted_text)
    report = compose_report(
        REPORT.text_report, findings, format_coverage(extraction), limit=REPORT_CHAR_LIMIT,
        automatic=format_automatic_checks(checks),
    )
    voice = compose_tts_script(
        REPORT.tts_script, automatic_failed=any(check.status is CheckStatus.FAIL for check in checks)
    )
    return {
        "sample_report.txt": (report + "\n").encode("utf-8"),
        "sample_voice_script.txt": (voice + "\n").encode("utf-8"),
        "sample_checklist.pdf": generate_checklist(REPORT.checklist_items, checks),
    }


def build_demo() -> dict[str, bytes]:
    """File name -> content of every demo file except the PNG preview (which needs Poppler and is not byte-stable)."""
    contract = _contract_pdf()
    return {"sample_contract.pdf": contract, **asyncio.run(_run_pipeline(contract))}


def render_preview(checklist_pdf: bytes) -> bytes:
    """First page of the checklist as PNG, for the README. Needs Poppler (a project requirement anyway)."""
    from pdf2image import convert_from_bytes

    page = convert_from_bytes(checklist_pdf, dpi=90, first_page=1, last_page=1)[0].convert("RGB")
    width, height = page.size
    # Crop the empty lower part of the page (above the page-number footer) so the README image is not mostly white.
    content_bottom = ImageOps.invert(page.crop((0, 0, width, int(height * 0.9))).convert("L")).getbbox()[3]
    preview = page.crop((0, 0, width, min(height, content_bottom + 40)))
    buffer = io.BytesIO()
    preview.quantize(colors=64).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> None:
    files = build_demo()
    files["sample_checklist.png"] = render_preview(files["sample_checklist.pdf"])
    for name, content in files.items():
        (DEMO_DIR / name).write_bytes(content)  # bytes: no platform line-ending conversion
        print(f"{name}: {len(content)} bytes")


if __name__ == "__main__":
    main()
