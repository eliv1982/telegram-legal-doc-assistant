"""
Labelled requisite extraction (Stage 5): only values that follow a label are found, from the document text alone,
with the analysed page they were found on. Ambiguity is a miss, never a guess.

The identifiers below are public ones (well-known organisations and documentation examples), not personal data.
"""
import pytest

from services.requisites import RequisiteKind as K
from services.requisites import extract_requisites

SBER_INN, FNS_INN = "7707083893", "7707329152"
SBER_KPP = "773601001"
SBER_BIK = "044525225"
ACCOUNT = "40702810300000012345"
CORR_ACCOUNT = "30101810400000000225"


def found(text: str, **kwargs) -> list[tuple[K, str]]:
    return [(requisite.kind, requisite.value) for requisite in extract_requisites(text, **kwargs)]


@pytest.mark.parametrize("text, expected", [
    pytest.param(f"инн № {SBER_INN}", [(K.INN, SBER_INN)], id="lowercase-nbsp-number-sign"),
    pytest.param(f"ИНН:\n{SBER_INN}", [(K.INN, SBER_INN)], id="value-on-the-next-line"),
    pytest.param("ИНН получателя: 7707 083893", [(K.INN, SBER_INN)], id="qualifier-and-grouped-digits"),
    pytest.param(f"ИНН/КПП {SBER_INN} / {SBER_KPP}", [(K.INN, SBER_INN), (K.KPP, SBER_KPP)], id="inn-kpp-pair"),
    pytest.param(f"ИНН/КПП: {SBER_INN}/{SBER_KPP}", [(K.INN, SBER_INN), (K.KPP, SBER_KPP)], id="pair-without-spaces"),
    pytest.param("ОГРН 1027700132195; ОГРНИП 304500116000157",
                 [(K.OGRN, "1027700132195"), (K.OGRNIP, "304500116000157")], id="ogrn-and-ogrnip"),
    pytest.param("БИК банка получателя: 044 525 225", [(K.BIK, SBER_BIK)], id="bik-grouped"),
    pytest.param(f"р/с 40702 810 3 0000 0012345, к/с {CORR_ACCOUNT}",
                 [(K.SETTLEMENT_ACCOUNT, ACCOUNT), (K.CORRESPONDENT_ACCOUNT, CORR_ACCOUNT)], id="account-abbreviations"),
    pytest.param(f"Расчётный счёт № {ACCOUNT}; корреспондентский счёт: {CORR_ACCOUNT}",
                 [(K.SETTLEMENT_ACCOUNT, ACCOUNT), (K.CORRESPONDENT_ACCOUNT, CORR_ACCOUNT)], id="account-full-words"),
])
def test_labelled_requisites_are_found_in_their_common_written_forms(text, expected):
    assert found(text) == expected


def test_numbers_without_a_requisite_label_are_never_picked_up():
    text = (
        f"Договор № {SBER_INN} от 01.02.2024. Тел.: +7 (495) 123-45-67, 8-800-555-35-35. "
        f"Сумма: 1 027 700 132 195,00 руб. Счёт {ACCOUNT} открыт в банке. Код {SBER_BIK}. "
        f"Иванов Р.С. 12 июня. ИННОВАЦИИ {SBER_INN}. ИНН считать корректным. БИК банка указан в приложении."
    )

    assert found(text) == []


def test_ambiguous_layouts_are_a_miss_not_a_guess():
    assert found(f"ИНН КПП\n{SBER_INN} {SBER_KPP}") == []  # header row: which value is which is not guessed
    assert found("ОГРН/ОГРНИП 1027700132195") == []  # two labels, one value
    assert found(f"ИНН/КПП {SBER_INN} / не указан") == [(K.INN, SBER_INN)]


def test_a_complete_number_is_not_extended_by_the_digits_that_follow_it():
    assert found(f"ИНН {SBER_INN} 12 августа 2024") == [(K.INN, SBER_INN)]
    assert found("ИНН 7707 083893") == [(K.INN, SBER_INN)]


def test_digits_are_never_corrected():
    # An OCR-style slip is reported as written; the validator, not the extractor, decides what that means.
    assert found("ИНН 77O7083893") == [(K.INN, "77O7083893")]
    assert found(f"ИНН {SBER_INN[:-1]}8") == [(K.INN, "7707083898")]


def test_page_markers_attribute_requisites_and_are_not_searchable_data():
    text = (
        f"[Страница 1]\nИНН {SBER_INN}\n\n[Страница 2]\nКПП {SBER_KPP}\nБИК {SBER_BIK}\n\n"
        f"[Страница 3]\nИНН {FNS_INN}\n[Страница 7707083893]"
    )

    pages = [(r.kind, r.value, r.page) for r in extract_requisites(text, analysed_pages=(1, 2, 3))]

    assert pages == [
        (K.INN, SBER_INN, 1), (K.KPP, SBER_KPP, 2), (K.BIK, SBER_BIK, 2), (K.INN, FNS_INN, 3),
    ]
    assert extract_requisites(f"ИНН {SBER_INN}")[0].page is None  # an image has no page markers
    assert found(f"[Страница 1]\nИНН\n\n[Страница 2]\n{SBER_INN}", analysed_pages=(1, 2)) == []  # no label borrows a value across pages


def test_a_forged_marker_can_never_assign_a_page_that_was_not_analysed():
    text = (
        f"[Страница 1]\nтекст\n[Страница 9]\nИНН {SBER_INN}\n\n"  # page 9 was not analysed
        f"[Страница 2]\nКПП {SBER_KPP}\n[Страница 1]\nБИК {SBER_BIK}\n"  # going back is not a page break either
        "[Страница " + "9" * 5000 + f"]\nИНН {FNS_INN}"  # absurd marker: ignored, and int() is never asked to parse it
    )

    pages = [(r.value, r.page) for r in extract_requisites(text, analysed_pages=(1, 2))]

    assert pages == [(SBER_INN, 1), (SBER_KPP, 2), (SBER_BIK, 2), (FNS_INN, 2)]
