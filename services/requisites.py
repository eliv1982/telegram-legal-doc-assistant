"""
Поиск реквизитов в извлечённом тексте документа. Только по меткам: число без метки реквизитом не считается,
сколько бы цифр в нём ни было (телефон, дата, номер договора, сумма и т. п. никогда не принимаются за ИНН).

Вход — только текст документа (extracted_text из services.document_extraction), а не ответы модели. Маркеры «[Страница N]», которые
код вставляет между страницами, служат лишь для указания страницы и сами в поиск не попадают. Страница принимается
только если она входит в число проанализированных, номера идут по возрастанию, а маркер занимает всю строку.
Это страховка от поддельных маркеров в тексте документа; но и без неё номер страницы лишь пояснение к результату,
на сам результат проверки он не влияет.

Значение не «исправляется»: убираются только пробелы-разделители групп цифр, и то лишь пока число не достигло
допустимой длины (так «ИНН 7707083893 12 августа» не превращается в 12-значный ИНН).
"""
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from enum import Enum


class RequisiteKind(str, Enum):
    INN = "inn"
    KPP = "kpp"
    OGRN = "ogrn"
    OGRNIP = "ogrnip"
    BIK = "bik"
    SETTLEMENT_ACCOUNT = "settlement_account"
    CORRESPONDENT_ACCOUNT = "correspondent_account"


@dataclass(frozen=True)
class Requisite:
    kind: RequisiteKind
    value: str  # как написано в документе, без пробелов между группами цифр
    page: int | None  # проанализированная страница; None, если в тексте нет маркеров страниц (изображение)


_ALNUM = "0-9A-Za-zА-Яа-яЁё"
# Значение длиннее 40 знаков реквизитом быть не может: такой «токен» не принимается вовсе, а не обрезается.
_TOKEN = rf"[{_ALNUM}]{{1,40}}(?![{_ALNUM}])"
_GAP = r"[\s:№#=–—\-.]{0,6}"  # между меткой и значением: пробелы, двоеточие, «№», тире и т. п.
_ADJACENT = re.compile(r"[\s:№#=–—\-./,\\]*")  # две метки подряд («ИНН КПП» в шапке таблицы): значения не угадываем
# Пояснение между меткой и значением («ИНН получателя»): закрытый список, а не любое слово.
_QUALIFIERS = (
    r"(?:\s+(?:получателя|плательщика|покупателя|продавца|поставщика|заказчика|исполнителя|подрядчика|"
    r"контрагента|организации|банка|стороны)){0,2}"
)

_SETTLEMENT = r"р\s*[/\\]\s*сч?(?:[её]т)?|расч[её]тн(?:ый|ого)\s+сч[её]т(?:а)?|расч\.\s*сч[её]т(?:а)?"
_CORRESPONDENT = (
    r"к\s*[/\\]\s*сч?(?:[её]т)?|кор\s*[/\\]\s*сч?(?:[её]т)?"
    r"|кор(?:р(?:еспондентск(?:ий|ого))?)?\.?\s*сч[её]т(?:а)?"
)

_LABEL = re.compile(
    r"(?<!\w)(?:"
    r"(?P<pair>ИНН\s*(?:[/\\,]|и)\s*КПП)"
    r"|(?P<ogrnip>ОГРНИП)|(?P<ogrn>ОГРН)|(?P<inn>ИНН)|(?P<kpp>КПП)|(?P<bik>БИК)"
    rf"|(?P<settlement>{_SETTLEMENT})|(?P<correspondent>{_CORRESPONDENT})"
    r")(?![^\W\d_])",  # метка — целое слово: «ИННОВАЦИИ» не метка
    re.IGNORECASE,
)
_VALUE = re.compile(rf"{_QUALIFIERS}{_GAP}(?P<first>{_TOKEN})", re.IGNORECASE)
_PAIR_VALUE = re.compile(rf"{_QUALIFIERS}{_GAP}(?P<first>{_TOKEN})(?:\s*[/\\]\s*(?P<second>{_TOKEN}))?", re.IGNORECASE)
_NEXT_GROUP = re.compile(rf"[   .\-]([0-9]+)(?![{_ALNUM}])")
_DIGITS = re.compile(r"[0-9]+")
_HAS_DIGIT = re.compile(r"[0-9]")
_PAGE_MARKER = re.compile(r"\[Страница (\d{1,4})\]")  # не больше 4 цифр: int() от гигантской строки не вызывается

_KIND_BY_GROUP = {
    "ogrnip": RequisiteKind.OGRNIP,
    "ogrn": RequisiteKind.OGRN,
    "inn": RequisiteKind.INN,
    "kpp": RequisiteKind.KPP,
    "bik": RequisiteKind.BIK,
    "settlement": RequisiteKind.SETTLEMENT_ACCOUNT,
    "correspondent": RequisiteKind.CORRESPONDENT_ACCOUNT,
}
# Допустимые длины целого числа: пока их нет, соседние группы цифр склеиваются; достигнув такой длины, число готово.
_FULL_LENGTHS = {
    RequisiteKind.INN: (10, 12),
    RequisiteKind.OGRN: (13,),
    RequisiteKind.OGRNIP: (15,),
    RequisiteKind.BIK: (9,),
    RequisiteKind.SETTLEMENT_ACCOUNT: (20,),
    RequisiteKind.CORRESPONDENT_ACCOUNT: (20,),
}


def extract_requisites(text: str, *, analysed_pages: Collection[int] | None = None) -> list[Requisite]:
    """Реквизиты с метками в порядке их появления в тексте. `analysed_pages=None` — страницы не фильтруются."""
    found: list[Requisite] = []
    for page, body in _split_pages(text, analysed_pages):
        found.extend(_scan(body, page))
    return found


def _split_pages(text: str, analysed_pages: Collection[int] | None) -> Iterator[tuple[int | None, str]]:
    page: int | None = None
    last = 0
    lines: list[str] = []
    for line in text.splitlines():
        marker = _PAGE_MARKER.fullmatch(line.strip())
        number = int(marker[1]) if marker else 0
        if marker and number > last and (analysed_pages is None or number in analysed_pages):
            yield page, "\n".join(lines)
            page, last, lines = number, number, []
        else:
            lines.append(line)
    yield page, "\n".join(lines)


def _scan(body: str, page: int | None) -> list[Requisite]:
    found: list[Requisite] = []
    previous_end: int | None = None
    for label in _LABEL.finditer(body):
        adjacent = previous_end is not None and _ADJACENT.fullmatch(body, previous_end, label.start())
        previous_end = label.end()
        if adjacent:
            continue
        if label.lastgroup == "pair":
            found.extend(_pair(body, label.end(), page))
            continue
        kind = _KIND_BY_GROUP[label.lastgroup]
        value = _VALUE.match(body, label.end())
        if value and _HAS_DIGIT.search(value["first"]):
            found.append(Requisite(kind, _join_groups(body, value.end(), value["first"], kind), page))
    return found


def _pair(body: str, start: int, page: int | None) -> list[Requisite]:
    """«ИНН/КПП 7707083893 / 773601001»: значения идут в порядке меток. Одно значение — это ИНН."""
    match = _PAIR_VALUE.match(body, start)
    if not match or not _HAS_DIGIT.search(match["first"]):
        return []
    found = [Requisite(RequisiteKind.INN, match["first"], page)]
    if match["second"] and _HAS_DIGIT.search(match["second"]):
        found.append(Requisite(RequisiteKind.KPP, match["second"], page))
    return found


def _join_groups(body: str, position: int, value: str, kind: RequisiteKind) -> str:
    """«40702 810 3 0000 0012345» -> одно число; склейка прекращается, когда число достигло допустимой длины."""
    lengths = _FULL_LENGTHS.get(kind)
    if lengths is None or not _DIGITS.fullmatch(value):
        return value
    while len(value) < max(lengths) and len(value) not in lengths:
        group = _NEXT_GROUP.match(body, position)
        if not group or len(value) + len(group[1]) > max(lengths):
            break
        value += group[1]
        position = group.end()
    return value
