"""
PDF-чек-лист. На входе только структура: пункты модели (ChecklistItem) и результаты автоматических проверок
(DeterministicCheck). Текст нигде не разбирается обратно, а результаты проверок модель не пересказывает: строку
о непрошедшей проверке пишет код (services.report.describe_check).

  1. «Автоматические проверки реквизитов: не пройдены» — только непрошедшие проверки (FAIL). Пройденные (PASS) и
     информационные (INFO) строки в чек-лист не попадают: они не действие, а шум. Раздела нет, если непрошедших нет.
  2. «Чек-лист модели» — пункты модели в порядке приоритета; приоритет остаётся видимой меткой пункта.

Заголовки разделов — заголовки, а не пункты: у них нет квадрата для отметки. Длинный пункт переносится по словам и
по страницам, а не обрезается. Шрифт один и поставляется с репозиторием (assets/fonts, PT Sans, SIL OFL): Cyrillic
не зависит от шрифтов машины. Символ, которого в шрифте нет, заменяется на «?», а не превращается в пустой квадрат.
"""
import io
import logging
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

from services.deterministic_checks import CheckStatus, DeterministicCheck
from services.report import AUTOMATIC_NOTE, AUTOMATIC_OCR_NOTE, describe_check
from services.schemas import PRIORITY_LABELS, PRIORITY_ORDER, ChecklistItem

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = "PTSans"
FONT_BOLD = "PTSans-Bold"
FONT_FILES = {FONT_REGULAR: "PT_Sans-Web-Regular.ttf", FONT_BOLD: "PT_Sans-Web-Bold.ttf"}
# Заменитель символа, которого нет в шрифте: виден читателю и не превращается в пустой квадрат.
REPLACEMENT = "?"

TITLE = "Чек-лист проверки документа"
SUBTITLE = (
    "Предварительный разбор (first-pass review), а не юридическая консультация. "
    "Пункты модели — её оценка: сверяйте их с самим документом."
)
AUTOMATIC_TITLE = "Автоматические проверки реквизитов: не пройдены"
AUTOMATIC_SOURCE = "Результат работы кода без участия ИИ."
MODEL_TITLE = "Чек-лист модели (предварительная оценка ИИ)"
NO_ITEMS_LINE = "Пунктов для проверки модель не предложила."
PAGE_LABEL = "Стр."


class ChecklistFontError(RuntimeError):
    """Поставляемый шрифт недоступен: чек-лист без него не рисуется (иначе кириллица стала бы квадратами)."""


@dataclass(frozen=True)
class ChecklistEntry:
    """Пункт с квадратом для отметки. `tag` — видимая метка перед текстом, например «[Высокий]»."""

    text: str
    tag: str = ""


@dataclass(frozen=True)
class ChecklistSection:
    """Раздел: заголовок (не пункт), пояснения обычным текстом без квадрата и пункты."""

    title: str
    entries: tuple[ChecklistEntry, ...] = ()
    notes: tuple[str, ...] = ()


def build_checklist(
    items: Sequence[ChecklistItem], checks: Sequence[DeterministicCheck] = (), *, ocr_used: bool = False
) -> list[ChecklistSection]:
    """
    Разделы чек-листа из структурных данных. Пункты модели идут в порядке приоритета (сортировка устойчива), без
    изменений текста; непрошедшие проверки — отдельным разделом перед ними. `ocr_used` — текст документа прочитан
    ИИ по изображению: тогда в разделе проверок сказано, что ошибка распознавания может дать ложное несовпадение.
    """
    sections: list[ChecklistSection] = []
    failed = [check for check in checks if check.status is CheckStatus.FAIL]
    if failed:
        notes = (AUTOMATIC_SOURCE, AUTOMATIC_NOTE, *((AUTOMATIC_OCR_NOTE,) if ocr_used else ()))
        sections.append(
            ChecklistSection(AUTOMATIC_TITLE, tuple(ChecklistEntry(describe_check(check)) for check in failed), notes)
        )
    ordered = sorted(items, key=lambda item: PRIORITY_ORDER.index(item.priority))
    entries = tuple(ChecklistEntry(item.text, tag=f"[{PRIORITY_LABELS[item.priority]}]") for item in ordered)
    sections.append(ChecklistSection(MODEL_TITLE, entries, notes=() if entries else (NO_ITEMS_LINE,)))
    return sections


@lru_cache(maxsize=1)
def _register_fonts() -> frozenset[int]:
    """Регистрирует поставляемый шрифт в ReportLab и возвращает коды символов, которые он умеет рисовать."""
    for name, filename in FONT_FILES.items():
        try:
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
        except Exception as e:
            raise ChecklistFontError(f"bundled font {filename} is unavailable") from e
    pdfmetrics.registerFontFamily(
        FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD, italic=FONT_REGULAR, boldItalic=FONT_BOLD
    )
    return frozenset(pdfmetrics.getFont(FONT_REGULAR).face.charToGlyph)


def printable(text: str, glyphs: frozenset[int]) -> str:
    """
    Текст, который шрифт нарисует целиком: нормализованный (NFC: «й» одним символом, а не «и» с краткой), с пробелами
    вместо переводов строк и неразрывных пробелов, без управляющих и невидимых символов (в том числе выбора
    начертания эмодзи), а каждый символ без глифа — заменён на REPLACEMENT.
    """
    out: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        if char.isspace():
            out.append(" ")
        elif unicodedata.category(char) in ("Cc", "Cf") or char in "︎️":
            continue
        else:
            out.append(char if ord(char) in glyphs else REPLACEMENT)
    return " ".join("".join(out).split())


_INK = colors.HexColor("#1F2933")
_MUTED = colors.HexColor("#52606D")


def _styles() -> dict[str, ParagraphStyle]:
    # splitLongWords: слово шире строки (адрес, номер без пробелов) переносится по символам, а не вылезает за поле.
    def style(name: str, **options) -> ParagraphStyle:
        defaults = {"fontName": FONT_REGULAR, "textColor": _INK, "splitLongWords": 1}
        return ParagraphStyle(name, **{**defaults, **options})

    return {
        "title": style("title", fontName=FONT_BOLD, fontSize=17, leading=21, spaceAfter=4),
        "subtitle": style("subtitle", textColor=_MUTED, fontSize=9.5, leading=13, spaceAfter=10),
        # Заголовок раздела — плашка (а не пункт с квадратом); keepWithNext не оставляет его одним внизу страницы.
        "heading": style(
            "heading", fontName=FONT_BOLD, fontSize=12.5, leading=16, spaceBefore=14, spaceAfter=8, keepWithNext=1,
            backColor=colors.HexColor("#E4E7EB"), borderPadding=(4, 6, 4, 6),
        ),
        "note": style("note", textColor=_MUTED, fontSize=9.5, leading=13, spaceAfter=3),
        "item": style("item", fontSize=11, leading=15),
    }


_BOX = 9.5  # сторона квадрата для отметки, pt
_INDENT = 18  # отступ текста пункта от левого края


class _ChecklistRow(Flowable):
    """
    Абзац с нарисованным квадратом в поле. Квадрат — фигура, а не символ шрифта: в шрифте нет глифа «☐», и пустой
    квадрат вместо него был бы неотличим от потерянного символа. Длинный пункт переносится на следующую страницу
    как обычный абзац; квадрат рисуется только у первой его части.
    """

    def __init__(self, paragraph: Paragraph, *, boxed: bool = True) -> None:
        super().__init__()
        self._paragraph = paragraph
        self._boxed = boxed
        self.spaceAfter = 5

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        _, height = self._paragraph.wrap(availWidth - _INDENT, availHeight)
        self.width, self.height = availWidth, height
        return self.width, self.height

    def split(self, availWidth: float, availHeight: float) -> list[Flowable]:
        parts = self._paragraph.split(availWidth - _INDENT, availHeight)
        return [_ChecklistRow(part, boxed=self._boxed and index == 0) for index, part in enumerate(parts)]

    def draw(self) -> None:
        self._paragraph.drawOn(self.canv, _INDENT, 0)
        if self._boxed:
            leading = self._paragraph.style.leading
            canvas = self.canv
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#323F4B"))
            canvas.setLineWidth(0.9)
            canvas.rect(1, self.height - leading + (leading - _BOX) / 2 + 0.5, _BOX, _BOX, stroke=1, fill=0)
            canvas.restoreState()


def _story(sections: Sequence[ChecklistSection], glyphs: frozenset[int]) -> list[Flowable]:
    """
    Содержимое страниц. Квадрат для отметки есть только у _ChecklistRow, а строки на него идут только из `entries`:
    заголовки и пояснения — обычные абзацы.
    """
    styles = _styles()

    def text(value: str) -> str:
        return escape(printable(value, glyphs))  # Paragraph читает XML-подобную разметку: «<» и «&» нужно экранировать

    story: list[Flowable] = [Paragraph(text(TITLE), styles["title"]), Paragraph(text(SUBTITLE), styles["subtitle"])]
    for section in sections:
        story.append(Paragraph(text(section.title), styles["heading"]))
        story.extend(Paragraph(text(note), styles["note"]) for note in section.notes)
        if section.notes and section.entries:
            story.append(Spacer(1, 4))
        for entry in section.entries:
            label = f"<b>{text(entry.tag)}</b> " if entry.tag else ""
            story.append(_ChecklistRow(Paragraph(label + text(entry.text), styles["item"])))
    return story


def render_checklist_pdf(sections: Sequence[ChecklistSection]) -> bytes:
    """PDF из готовых разделов. Переносит строки и страницы сам; текст предварительно приводится к printable()."""
    glyphs = _register_fonts()
    story = _story(sections, glyphs)

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(FONT_REGULAR, 9)
        canvas.setFillColor(colors.HexColor("#7B8794"))
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"{PAGE_LABEL} {document.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title=printable(TITLE, glyphs), author="", invariant=1,  # invariant: одинаковый вход даёт одинаковые байты
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def generate_checklist(
    items: Sequence[ChecklistItem], checks: Sequence[DeterministicCheck] = (), *, ocr_used: bool = False
) -> bytes:
    """Байты PDF-чек-листа по структурным данным (блокирующая: вызывать через asyncio.to_thread)."""
    return render_checklist_pdf(build_checklist(items, checks, ocr_used=ocr_used))
