"""
Сокращение текста по границам, а не по счётчику символов. Срез `text[:n]` режет слово пополам и теряет конец мысли;
здесь текст обрывается там, где закончился абзац, строка, предложение или хотя бы слово.
"""
import re

# Конец предложения: знак (с закрывающей кавычкой или скобкой), за которым идёт пробел и начало нового предложения —
# заглавная буква, открывающая кавычка или скобка. Так «п. 4.2», «ст. 5» и «т. е. срок» концом предложения не считаются.
_SENTENCE_END = re.compile(r"[.!?…][»”\")\]]*(?=\s+[«“\"(\[A-ZА-ЯЁ])")
_WHITESPACE = re.compile(r"\s")
# Граница абзаца, строки или предложения принимается, только если после неё остаётся хотя бы эта доля допустимой длины:
# иначе из-за одного раннего перевода строки пропала бы почти вся полезная часть.
MIN_KEPT = 0.5
_TRAILING_PUNCTUATION = ",;:—–- "


def split_sentences(text: str) -> list[str]:
    """Предложения текста по правилу _SENTENCE_END; пробелы по краям убираются, пустых предложений нет."""
    sentences, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        sentences.append(text[start : match.end()].strip())
        start = match.end()
    sentences.append(text[start:].strip())
    return [sentence for sentence in sentences if sentence]


def fit_text(text: str, limit: int, *, marker: str = "") -> str:
    """
    Текст не длиннее `limit` знаков вместе с `marker`. Если он короче, возвращается как есть; иначе обрывается на
    лучшей из границ, которая оставляет не менее половины допустимой длины: абзац, строка, предложение. Если такой нет,
    обрыв идёт по границе слова, и только слово длиннее всей допустимой длины режется посередине (иначе нечем).
    К сокращённому тексту дописывается `marker`.
    """
    if len(text) <= limit:
        return text
    window = limit - len(marker)
    if window <= 0:
        return marker.strip()[: max(limit, 0)]
    return text[: _cut_point(text, window)].rstrip() + marker


def _cut_point(text: str, window: int) -> int:
    floor = int(window * MIN_KEPT)
    for separator in ("\n\n", "\n"):
        index = text.rfind(separator, 0, window + 1)
        if index >= floor:
            return index
    sentence_end = 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() > window:
            break
        sentence_end = match.end()
    if sentence_end >= floor and sentence_end > 0:
        return sentence_end
    spaces = [match.start() for match in _WHITESPACE.finditer(text, 0, window + 1)]
    if spaces and spaces[-1] > 0:
        return spaces[-1]
    return window


def fit_words(text: str, max_words: int) -> str:
    """
    Текст не длиннее `max_words` слов, обрезанный по границе предложения: целиком остаются первые предложения, что
    уместились. Если не уместилось даже первое, берутся первые слова (слово не режется) и ставится точка.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    kept: list[str] = []
    used = 0
    for sentence in split_sentences(text):
        count = len(sentence.split())
        if used + count > max_words:
            break
        kept.append(sentence)
        used += count
    if kept:
        return " ".join(kept)
    return " ".join(words[:max_words]).rstrip(_TRAILING_PUNCTUATION) + "."
