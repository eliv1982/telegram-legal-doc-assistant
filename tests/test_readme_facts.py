"""
The README's concrete claims stay true: its limits match services/limits.py, every environment variable is documented,
its relative links resolve, its output excerpt is real output, and the retired names and claims do not come back.
"""
import re
from pathlib import Path

from services import limits

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_limits_table_matches_the_code():
    from handlers.document import REPORT_CHAR_LIMIT

    mib = 1024 * 1024
    expected = [
        f"| Maximum upload size | {limits.MAX_UPLOAD_BYTES // mib} MB |",
        f"| PDF pages accepted | up to {limits.MAX_PDF_PAGES} |",
        f"| PDF pages **analysed** | the first {limits.MAX_ANALYSED_PAGES} |",
        f"| PDF page size | {limits.MIN_PAGE_POINTS:g} to {limits.MAX_PAGE_POINTS:g} pt per side",
        f"| Image size | up to {limits.MAX_IMAGE_PIXELS // 1_000_000} megapixels, at least {limits.MIN_IMAGE_SIDE_PX} px per side |",
        f"re-saved as JPEG with the long side at most {limits.MAX_IMAGE_SIDE_PX} px",
        f"(long side at most {limits.RENDER_LONG_SIDE_PX} px, a {limits.RENDER_TIMEOUT_SECONDS} s timeout per call)",
        f"at most {REPORT_CHAR_LIMIT} characters",
        f"the first {limits.MAX_ANALYSED_PAGES} pages of a PDF",
    ]
    missing = [claim for claim in expected if claim not in README]
    assert not missing, f"README disagrees with services/limits.py: {missing}"


def test_every_environment_variable_is_documented_in_the_readme_and_env_example():
    names = re.findall(r'getenv\("([A-Z_]+)"', (ROOT / "config.py").read_text(encoding="utf-8"))
    assert len(names) >= 10  # the scan is not vacuous
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in names:
        assert f"`{name}`" in README, f"{name} is not documented in the README"
        assert name in example, f"{name} is missing from .env.example"


def test_relative_links_in_the_readmes_resolve():
    for readme in (ROOT / "README.md", ROOT / "docs" / "demo" / "README.md"):
        text = readme.read_text(encoding="utf-8")
        targets = [t for t in re.findall(r"\]\(([^)\s]+)\)", text) if not re.match(r"[a-z]+:|#", t)]
        assert targets, readme.name
        for target in targets:
            assert (readme.parent / target.split("#")[0]).exists(), f"{readme.name}: broken link {target}"


def test_the_readme_output_excerpt_is_real_output():
    block = re.search(r"```text\n(🔎.*?)```", README, re.DOTALL)
    assert block, "the output excerpt was not found"
    produced = set((ROOT / "docs" / "demo" / "sample_report.txt").read_text(encoding="utf-8").splitlines())
    lines = [line for line in block[1].splitlines() if line.strip() and line.strip() != "[…]"]
    assert len(lines) > 10
    assert [line for line in lines if line not in produced] == []


def test_retired_names_and_claims_are_gone_from_the_readme_and_project_metadata():
    metadata = README + (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # (gTTS is mentioned on purpose, as a negation in the privacy section; its absence from the code is tested elsewhere.)
    for retired in ("telegram-legal-doc-assistant", "GPT-4 Vision", "CHECKLIST_FORMAT", "requirements.txt"):
        assert retired not in metadata, retired
    assert "not legal advice" in README.lower()
