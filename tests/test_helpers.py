"""
Characterization of the pure helpers in utils/helpers.py: current behavior that is correct and should stay.
"""
from utils.helpers import extract_json_from_text, parse_confidence, parse_response_sections


def test_parse_response_sections_with_and_without_end_markers():
    with_end_markers = (
        "=== TEXT_REPORT ===\nreport\n=== END_TEXT_REPORT ===\n"
        "=== TTS_SCRIPT ===\nspoken\n=== END_TTS_SCRIPT ===\n"
        "=== CHECKLIST ===\n□ one\n□ two\n=== END_CHECKLIST ==="
    )
    assert parse_response_sections(with_end_markers) == {
        "text_report": "report",
        "tts_script": "spoken",
        "checklist": "□ one\n□ two",
    }

    # END markers missing: each block runs up to the next block's start marker.
    without_end_markers = "=== TEXT_REPORT ===\nreport\n=== TTS_SCRIPT ===\nspoken\n=== CHECKLIST ===\n□ one"
    assert parse_response_sections(without_end_markers) == {
        "text_report": "report",
        "tts_script": "spoken",
        "checklist": "□ one",
    }


def test_extract_json_from_text_unwraps_fenced_json_and_rejects_prose():
    fenced = 'Here you go:\n```json\n{"document_type": "Договор", "confidence": 92}\n```'
    assert extract_json_from_text(fenced) == {"document_type": "Договор", "confidence": 92}
    assert extract_json_from_text("no json here") is None
    assert extract_json_from_text("{not valid json}") is None


def test_parse_confidence_valid_inputs():
    assert parse_confidence(85) == 85
    assert parse_confidence("85%") == 85
    assert parse_confidence(" 92 ") == 92
    assert parse_confidence(150) == 100  # clamped
    assert parse_confidence(-5) == 0  # clamped
