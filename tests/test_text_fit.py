"""
utils/text.py (Stage 6): text is shortened at a paragraph, line, sentence or word boundary, never by a character count
that lands in the middle of a word.
"""
from utils.text import fit_text, fit_words, split_sentences

PROSE = (
    "Договор аренды заключён на пять лет. Арендная плата составляет 120 000 ₽ в месяц, п. 4.2 допускает индексацию. "
    "Срок оплаты — до десятого числа. Неустойка не ограничена: это главный риск. Рекомендуем согласовать предел."
)
WORDS = set(PROSE.split())


def test_the_cut_never_lands_inside_a_word_whatever_the_limit():
    for limit in range(30, len(PROSE)):
        shortened = fit_text(PROSE, limit)
        assert len(shortened) <= limit
        assert set(shortened.split()) <= WORDS, (limit, shortened)  # every remaining word is a whole original word


def test_a_paragraph_boundary_beats_a_sentence_and_a_sentence_beats_a_word():
    paragraphs = "Первый абзац из двух слов.\n\nВторой абзац. С двумя предложениями.\n\nТретий абзац целиком не влезет."
    assert fit_text(paragraphs, 45, marker="…") == "Первый абзац из двух слов.…"  # paragraph end
    assert fit_text(PROSE, 60, marker="…") == "Договор аренды заключён на пять лет.…"  # sentence end, not "Арендная"
    assert fit_text("слово " * 20, 25) == "слово слово слово слово"  # no better boundary: the last whole word
    assert fit_text("short", 100) == "short"


def test_only_a_single_token_longer_than_the_limit_is_cut_in_the_middle_and_the_marker_always_fits():
    shortened = fit_text("x" * 5000, 100, marker="\n…")
    assert len(shortened) == 100 and shortened.endswith("\n…") and set(shortened[:-2]) == {"x"}
    assert fit_text("слово", 3, marker="длиннее лимита") == "дли"  # degenerate: only the marker's start fits


def test_the_sentence_rule_does_not_split_after_abbreviations_or_clause_numbers():
    text = "Согласно п. 4.2 договора срок — 30 дней, т. е. месяц. Оплата по ст. 5 допускается. Срок продлевается."
    assert split_sentences(text) == [
        "Согласно п. 4.2 договора срок — 30 дней, т. е. месяц.",
        "Оплата по ст. 5 допускается.",
        "Срок продлевается.",
    ]


def test_words_budget_keeps_whole_sentences_and_falls_back_to_whole_words():
    assert fit_words(PROSE, 100) == PROSE
    kept = fit_words(PROSE, 22)
    assert kept.endswith("п. 4.2 допускает индексацию.") and len(kept.split()) <= 22 and PROSE.startswith(kept)
    first_only = fit_words("Очень длинное единственное предложение без точки внутри которого много слов", 4)
    assert first_only == "Очень длинное единственное предложение."  # the first sentence alone is over budget: whole words, then a full stop
