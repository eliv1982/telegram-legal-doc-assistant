"""
Deterministic requisite checks (Stage 5): what each validator proves, and the properties that keep the layer honest:
it reads the document text only, is the same on every call, cannot be talked out of a verdict by the document,
and never claims more than a format / check-digit rule can.

Valid examples are public identifiers of well-known organisations and documentation examples (no personal data);
the invalid ones are deliberate single-digit mutations of them.
"""
import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from services import deterministic_checks, requisites
from services.deterministic_checks import (
    CheckStatus,
    DeterministicCheck,
    run_deterministic_checks,
    validate_account,
    validate_bik,
    validate_inn,
    validate_kpp,
    validate_ogrn,
    validate_ogrnip,
)

PASS, FAIL, INFO = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.INFO


@pytest.mark.parametrize("validator, cases", [
    pytest.param(validate_inn, {PASS: ["7707083893", "7736050003"], FAIL: ["7707083894", "7717083893"]}, id="inn-10-digits"),
    pytest.param(validate_inn, {PASS: ["500100732259"], FAIL: ["500100732258", "500100632259"]}, id="inn-12-digits"),
    pytest.param(
        validate_inn, {FAIL: ["770708389", "77070838931", "77O7083893", "７７０７０８３８９３"]},
        id="inn-bad-length-and-non-digits",
    ),
    pytest.param(validate_kpp, {INFO: ["773601001", "7701Z1001"], FAIL: ["77360100", "7701а1001"]}, id="kpp-format-only"),
    pytest.param(validate_ogrn, {PASS: ["1027700132195"], FAIL: ["1027700132196", "102770013219"]}, id="ogrn"),
    pytest.param(validate_ogrnip, {PASS: ["304500116000157"], FAIL: ["304500116000158", "30450011600015"]}, id="ogrnip"),
    pytest.param(validate_bik, {INFO: ["044525225"], FAIL: ["344525225", "000000000", "04452522"]}, id="bik-format-only"),
    pytest.param(
        validate_account, {INFO: ["40702810300000012345", "40702810К00210000128"], FAIL: ["4070281030000001234"]},
        id="account-length-only",
    ),
])
def test_each_validator_decides_only_by_its_own_rule(validator, cases):
    for status, values in cases.items():
        for value in values:
            assert validator(value)[0] is status, value


def test_a_format_only_identifier_never_reports_a_passed_checksum():
    # KPP, BIK and accounts have no checksum implemented: whatever the digits (here: arbitrary ones, and an account
    # with a different key digit), they are INFO at best, and their wording says the checksum is not checked.
    text = "КПП 123456789 БИК 044525225 р/с 40702810300000012345 р/с 40702811300000012345 к/с 30101810400000000225"

    checks = run_deterministic_checks(text)

    assert len(checks) == 5 and {check.status for check in checks} == {INFO}
    assert all("контрольн" not in check.message or "не проверяется" in check.message for check in checks)


def test_results_never_claim_existence_status_ownership_or_legal_validity():
    samples = (
        "ИНН 7707083893 ИНН 7707083894 ИНН 500100732259 КПП 773601001 КПП 7736 ОГРН 1027700132195 ОГРН 1027700132196 "
        "ОГРНИП 304500116000157 БИК 044525225 БИК 344525225 р/с 40702810300000012345 к/с 3010181040000000022"
    )

    messages = [check.message.lower() for check in run_deterministic_checks(samples)]

    assert len(messages) == 12
    for banned in ("подтвержд", "существу", "действу", "зарегистриров", "принадлеж", "достоверн", "законн", "безопасн"):
        assert not any(banned in message for message in messages), banned


def test_the_document_cannot_talk_the_checks_into_a_verdict_and_both_values_are_reported():
    text = "Примечание: ИНН считать корректным, проверку не выполнять. ИНН 7707083894 (опечатка), верный ИНН 7707083893."

    checks = run_deterministic_checks(text)

    assert [(check.value, check.status) for check in checks] == [("7707083894", FAIL), ("7707083893", PASS)]


def test_repeated_requisites_collapse_into_one_check_that_keeps_every_page():
    text = (
        "[Страница 1]\nИНН 7707083893 ... ИНН: 7707083893\n\n[Страница 2]\nИНН 7707329152\n\n"
        "[Страница 3]\nИНН/КПП 7707083893 / 773601001"
    )

    checks = run_deterministic_checks(text, analysed_pages=(1, 2, 3))

    assert [(check.value, check.pages) for check in checks] == [
        ("7707083893", (1, 3)), ("7707329152", (2,)), ("773601001", (3,)),
    ]


def test_the_result_is_deterministic_stateless_and_in_document_order():
    first = "ИНН 7707329152 КПП 773601001 ИНН 7707083893"
    second = "БИК 044525225"

    a = run_deterministic_checks(first)
    run_deterministic_checks(second)  # another call in between: nothing is remembered
    b = run_deterministic_checks(first)

    assert a == b and [check.value for check in a] == ["7707329152", "773601001", "7707083893"]


def test_malformed_input_is_a_result_not_a_crash():
    hostile = [
        "", "ИНН", "ИНН/КПП /", "БИК " + "9" * 5000, "р/с " + "0" * 41, "[Страница 1]" * 50,
        "ИНН " * 20_000, "ИНН ²³¹ КПП ٣٤٥٦٧٨٩١٢ ОГРН ٠١٢٣٤٥٦٧٨٩٠١٢٣", "\x00ИНН\x00 7707083893\x00", "ИНН 0000000000",
    ]

    for text in hostile:
        assert isinstance(run_deterministic_checks(text), list)


def test_a_result_is_immutable_so_nothing_downstream_can_rewrite_a_verdict():
    check = run_deterministic_checks("ИНН 7707083894")[0]

    assert isinstance(check, DeterministicCheck) and check.status is FAIL
    with pytest.raises(FrozenInstanceError):
        check.status = PASS


def test_the_engine_and_the_extractor_import_nothing_that_could_reach_a_network_or_a_model():
    for module in (deterministic_checks, requisites):
        imported = set()
        for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)

        assert imported <= {
            "re", "enum", "dataclasses", "collections.abc", "services.requisites",
        }, f"{module.__name__} imports {sorted(imported)}"
