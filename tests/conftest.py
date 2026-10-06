"""
Offline test setup: no secrets, no network.
"""
import ipaddress
import os
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import dotenv
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from tests.fakes import FakeBot, FakeOpenAIService, FakeTTSService

# config.py calls load_dotenv() at import time. Stub it before anything imports `config`, so a developer's
# real .env (with live secrets) can never leak into the test process, and drop any ambient settings.
dotenv.load_dotenv = lambda *args, **kwargs: False
for _name in (
    "BOT_TOKEN",
    "OPENAI_API_KEY",
    "TTS_PROVIDER",
    "SESSION_TIMEOUT_MINUTES",
    "CHECKLIST_FORMAT",
    "CONFIDENCE_THRESHOLD",
):
    os.environ.pop(_name, None)


def _is_local(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """
    Telegram, OpenAI and Google (gTTS) are all reached by hostname, so refusing DNS lookups and
    non-loopback connects for every test is enough. Loopback stays open because asyncio's event loop
    uses it internally (socketpair on Windows).
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(host) -> None:
        raise RuntimeError(f"network access blocked in tests (attempted: {host!r})")

    def guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
            refuse(address[0])
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
            refuse(address[0])
        return real_connect_ex(self, address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@dataclass
class HandlerEnv:
    """handlers.document wired to fakes, with every temp file the bot creates redirected into `temp_dir`."""

    bot: FakeBot
    temp_dir: Path
    document: object  # the handlers.document module
    pipeline_calls: list[int] = field(default_factory=list)
    storage: MemoryStorage = field(default_factory=MemoryStorage)

    def new_state(self, user_id: int = 1) -> FSMContext:
        return FSMContext(storage=self.storage, key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))

    def leftover_files(self) -> list[str]:
        return sorted(p.name for p in self.temp_dir.iterdir())


@pytest.fixture
def handler_env(monkeypatch, tmp_path) -> HandlerEnv:
    import config
    from handlers import document

    # The handlers build files under tempfile.gettempdir(); point it at the per-test directory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(config, "CONFIDENCE_THRESHOLD", 70)
    monkeypatch.setattr(document, "OpenAIService", lambda **kwargs: FakeOpenAIService())
    monkeypatch.setattr(document, "TTSService", lambda **kwargs: FakeTTSService())
    monkeypatch.setattr(document, "generate_checklist", lambda text, output_format="pdf": b"%PDF-placeholder")

    env = HandlerEnv(bot=FakeBot(), temp_dir=tmp_path, document=document)
    real_run_pipeline = document.run_pipeline

    async def counting_run_pipeline(*args, **kwargs):
        env.pipeline_calls.append(1)
        return await real_run_pipeline(*args, **kwargs)

    monkeypatch.setattr(document, "run_pipeline", counting_run_pipeline)
    return env
