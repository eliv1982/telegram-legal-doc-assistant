"""
TTS is OpenAI-only (Stage 2): the gTTS / Google Translate data path is gone, not merely switched off.
"""
import importlib
import inspect
import sys
from pathlib import Path

import config
from services.tts_service import TTSService
from tests.fakes import ScriptedOpenAI, speech_response

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_CODE_DIRS = ("handlers", "prompts", "services", "states", "utils")


async def test_speech_is_synthesised_only_through_the_shared_openai_client():
    openai = ScriptedOpenAI(speech_response())  # the real SDK, a scripted transport

    audio = await TTSService(openai.client).text_to_speech("Резюме анализа")

    assert audio == b"ID3placeholder"
    assert openai.paths == ["/v1/audio/speech"]
    assert openai.json_bodies() == [{"model": "tts-1", "voice": "alloy", "input": "Резюме анализа"}]
    assert list(inspect.signature(TTSService.__init__).parameters) == ["self", "client"]  # no provider switch


def test_gtts_and_google_translate_are_gone_from_code_config_and_dependencies():
    for module in ("bot", "handlers.document", "services.tts_service"):
        importlib.import_module(module)
    assert "gtts" not in sys.modules
    assert not hasattr(config, "TTS_PROVIDER")

    runtime_files = [ROOT / "bot.py", ROOT / "config.py"]
    runtime_files += [path for directory in RUNTIME_CODE_DIRS for path in (ROOT / directory).rglob("*.py")]
    assert any(path.name == "tts_service.py" for path in runtime_files)  # the scan is not vacuous
    for path in runtime_files + [ROOT / "pyproject.toml", ROOT / ".env.example"]:
        text = path.read_text(encoding="utf-8").lower()
        assert "gtts" not in text and "google" not in text, path.name

    assert "gtts" not in (ROOT / "uv.lock").read_text(encoding="utf-8").lower()
