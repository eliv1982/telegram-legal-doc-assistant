"""
Автоматические проверки реквизитов: чистый код по тексту документа, без ИИ, без сети, без состояния.

Что проверяется (правила сверены с первоисточниками, см. README, «Автоматические проверки»):
  ИНН (10 и 12 цифр) .... длина и контрольное число (алгоритм ФНС России);
  ОГРН (13), ОГРНИП (15) . длина и контрольное число (приказ Минфина России № 165н, п. 7);
  КПП ................... только формат (9 знаков); контрольного числа у КПП нет, и оно не выдумывается;
  БИК ................... только формат (структура БИК по приложению 5 к Положению Банка России № 732-П);
  р/с, к/с .............. только длина (20 знаков, приложение 1 к приложению к Положению Банка России № 809-П).

Чего проверка НЕ доказывает: что налогоплательщик, организация или банк существуют и действуют, что номер
принадлежит названной стороне, что документ имеет юридическую силу. Реестры (ФНС, ЕГРЮЛ, справочник БИК) не опрашиваются.
PASS значит лишь «прошло именно это правило формата и контрольного числа»; FAIL — «именно это правило не выполнено»
(в том числе из-за ошибки распознавания скана); INFO — формат без контрольного числа.

Результат не зависит от ответов модели: на входе только текст документа.
"""
import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from services.requisites import RequisiteKind, extract_requisites


class CheckStatus(str, Enum):
    PASS = "pass"  # формат и контрольное число соответствуют правилу
    FAIL = "fail"  # правило не выполнено
    INFO = "info"  # формат соответствует, но контрольного числа для этого реквизита нет или оно не проверяется


@dataclass(frozen=True)
class DeterministicCheck:
    kind: RequisiteKind
    label: str  # как реквизит называется в отчёте
    value: str  # как написано в документе (без пробелов между группами цифр)
    status: CheckStatus
    message: str
    pages: tuple[int, ...] = ()  # проанализированные страницы, где значение встретилось; () — маркеров страниц нет


Verdict = tuple[CheckStatus, str]

LABELS = {
    RequisiteKind.INN: "ИНН",
    RequisiteKind.KPP: "КПП",
    RequisiteKind.OGRN: "ОГРН",
    RequisiteKind.OGRNIP: "ОГРНИП",
    RequisiteKind.BIK: "БИК",
    RequisiteKind.SETTLEMENT_ACCOUNT: "р/с",
    RequisiteKind.CORRESPONDENT_ACCOUNT: "к/с",
}

_DIGITS = re.compile(r"[0-9]+")  # именно ASCII: str.isdigit() принимает и «²», и арабо-индийские цифры
_KPP = re.compile(r"[0-9]{4}[0-9A-Z]{2}[0-9]{3}")  # NNNN PP XXX, P — цифра или заглавная латинская буква
_BIK = re.compile(r"[012][0-9]{8}")

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS = ((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))

_NOT_DIGITS: Verdict = (CheckStatus.FAIL, "значение должно состоять только из цифр.")


def run_deterministic_checks(
    extracted_text: str, *, analysed_pages: Collection[int] | None = None
) -> list[DeterministicCheck]:
    """
    Проверки реквизитов с метками, найденных в тексте документа. Порядок — по первому появлению в тексте, так что
    повторный вызов с тем же текстом даёт тот же список. Одинаковые реквизиты схлопываются в одну проверку,
    а страницы, где они встретились, сохраняются. Два разных значения под одной меткой остаются двумя проверками:
    неверное не отменяет верное и наоборот.
    """
    pages_by_requisite: dict[tuple[RequisiteKind, str], set[int]] = {}
    for requisite in extract_requisites(extracted_text, analysed_pages=analysed_pages):
        pages = pages_by_requisite.setdefault((requisite.kind, requisite.value), set())
        if requisite.page is not None:
            pages.add(requisite.page)
    return [
        _check(kind, value, tuple(sorted(pages))) for (kind, value), pages in pages_by_requisite.items()
    ]


def _check(kind: RequisiteKind, value: str, pages: tuple[int, ...]) -> DeterministicCheck:
    status, message = _VALIDATORS[kind](value)
    return DeterministicCheck(kind, LABELS[kind], value, status, message, pages)


def _control_digit(digits: str, weights: tuple[int, ...]) -> int:
    """Младший разряд остатка от деления суммы произведений цифр на веса на 11."""
    return sum(int(digit) * weight for digit, weight in zip(digits, weights)) % 11 % 10


def validate_inn(value: str) -> Verdict:
    if not _DIGITS.fullmatch(value):
        return _NOT_DIGITS
    if len(value) == 10:
        ok = _control_digit(value[:9], _INN10_WEIGHTS) == int(value[9])
        return (CheckStatus.PASS, "контрольное число корректно.") if ok else (
            CheckStatus.FAIL, "контрольное число не совпадает."
        )
    if len(value) == 12:
        first, second = _INN12_WEIGHTS
        ok = _control_digit(value[:10], first) == int(value[10]) and _control_digit(value[:11], second) == int(value[11])
        return (CheckStatus.PASS, "контрольные числа корректны.") if ok else (
            CheckStatus.FAIL, "контрольные числа не совпадают."
        )
    return CheckStatus.FAIL, f"ожидается 10 или 12 цифр, найдено знаков: {len(value)}."


def _validate_registration_number(value: str, length: int, modulus: int) -> Verdict:
    """ОГРН / ОГРНИП: последняя цифра — младший разряд остатка от деления предыдущего числа на 11 / 13."""
    if not _DIGITS.fullmatch(value):
        return _NOT_DIGITS
    if len(value) != length:
        return CheckStatus.FAIL, f"ожидается {length} цифр, найдено знаков: {len(value)}."
    if int(value[:-1]) % modulus % 10 == int(value[-1]):
        return CheckStatus.PASS, "контрольное число корректно."
    return CheckStatus.FAIL, "контрольное число не совпадает."


def validate_ogrn(value: str) -> Verdict:
    return _validate_registration_number(value, 13, 11)


def validate_ogrnip(value: str) -> Verdict:
    return _validate_registration_number(value, 15, 13)


def validate_kpp(value: str) -> Verdict:
    if _KPP.fullmatch(value):
        return CheckStatus.INFO, "формат соответствует ожидаемому; контрольная сумма для КПП не проверяется."
    return CheckStatus.FAIL, (
        "формат не соответствует: ожидается 9 знаков (4 цифры, 2 цифры или заглавные латинские буквы, 3 цифры)."
    )


def validate_bik(value: str) -> Verdict:
    if _BIK.fullmatch(value) and value[1:] != "0" * 8:
        return CheckStatus.INFO, "формат соответствует структуре БИК; контрольная сумма для БИК не проверяется."
    return CheckStatus.FAIL, "формат не соответствует: ожидается 9 цифр, первая — 0, 1 или 2."


def validate_account(value: str) -> Verdict:
    """
    Только длина. Защитный ключ (9-й знак) не проверяется: его алгоритм действующее Положение № 809-П не приводит
    (отсылает к «нормативным актам Банка России»), а связь счёта с БИК в свободном тексте документа неоднозначна.
    В примерах прежней схемы нумерации (Положение № 579-П, приложение 1) в позиции ключа стоит буква, поэтому
    нецифровое значение длиной 20 знаков — не ошибка (INFO), а неверная длина — ошибка.
    """
    if len(value) != 20:
        return CheckStatus.FAIL, f"ожидается 20 знаков, найдено знаков: {len(value)}."
    if _DIGITS.fullmatch(value):
        return CheckStatus.INFO, "20 цифр; контрольный ключ счёта не проверяется."
    return CheckStatus.INFO, "20 знаков, не все цифры; формат счёта и контрольный ключ не проверяются."


_VALIDATORS = {
    RequisiteKind.INN: validate_inn,
    RequisiteKind.KPP: validate_kpp,
    RequisiteKind.OGRN: validate_ogrn,
    RequisiteKind.OGRNIP: validate_ogrnip,
    RequisiteKind.BIK: validate_bik,
    RequisiteKind.SETTLEMENT_ACCOUNT: validate_account,
    RequisiteKind.CORRESPONDENT_ACCOUNT: validate_account,
}
