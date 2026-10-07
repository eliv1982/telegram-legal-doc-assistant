"""
docs/demo/: the committed demo is what the code produces today, it is synthetic, and generating it reads no .env.
"""
import io
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

from docs.demo import generate_demo
from services.deterministic_checks import run_deterministic_checks
from services.requisites import RequisiteKind

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demo"


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture(scope="module")
def generated() -> dict[str, bytes]:
    return generate_demo.build_demo()


@pytest.mark.parametrize("name", ["sample_report.txt", "sample_voice_script.txt"])
def test_committed_text_samples_are_what_the_code_produces(generated, name):
    committed = (DEMO / name).read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout may convert line endings
    assert committed == generated[name], f"docs/demo/{name} is stale: run `uv run python -m docs.demo.generate_demo`"


@pytest.mark.parametrize("name", ["sample_contract.pdf", "sample_checklist.pdf"])
def test_committed_pdf_samples_have_the_text_the_code_produces(generated, name):
    # Compared by extracted text, not bytes: the point is the content, not the PDF writer's serialisation.
    assert pdf_text((DEMO / name).read_bytes()) == pdf_text(generated[name]), f"docs/demo/{name} is stale"


def test_the_preview_image_is_committed_and_is_a_png():
    assert (DEMO / "sample_checklist.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_demo_requisites_cannot_belong_to_real_entities():
    # INN region code 00 and an OGRN starting with 0 do not exist in reality; the values are valid by checksum only.
    checks = run_deterministic_checks(pdf_text((DEMO / "sample_contract.pdf").read_bytes()))
    by_kind = {kind: [check.value for check in checks if check.kind is kind] for kind in RequisiteKind}
    assert by_kind[RequisiteKind.INN] and by_kind[RequisiteKind.OGRN]  # the scan is not vacuous
    assert all(value.startswith("00") for value in by_kind[RequisiteKind.INN])
    assert all(value.startswith("0") for value in by_kind[RequisiteKind.OGRN])
    assert not by_kind[RequisiteKind.OGRNIP] and not by_kind[RequisiteKind.BIK]
    assert not by_kind[RequisiteKind.SETTLEMENT_ACCOUNT] and not by_kind[RequisiteKind.CORRESPONDENT_ACCOUNT]


def test_the_demo_shows_all_three_evidence_outcomes_and_a_failed_check(generated):
    report = generated["sample_report.txt"].decode("utf-8")
    assert "❌ ИНН 0076543216" in report and "✅ ИНН 0012345673" in report  # the buyer's typo is caught by code
    assert report.count("«За просрочку оплаты") == 1  # one verbatim quote is shown as a quotation
    assert "Для 2 из 3 замечаний нет подтверждающей цитаты" in report  # the paraphrase and the null are not


def test_the_generator_uses_the_report_limit_of_the_real_pipeline():
    from handlers.document import REPORT_CHAR_LIMIT

    assert generate_demo.REPORT_CHAR_LIMIT == REPORT_CHAR_LIMIT


def test_generating_the_demo_does_not_import_config_and_so_never_reads_dotenv():
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import docs.demo.generate_demo; "
        "print('config' in sys.modules or 'dotenv' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(ROOT)], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.strip() == "False"
