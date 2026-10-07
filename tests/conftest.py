"""
Offline test setup: no secrets, no network.
"""
import asyncio
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

from tests.fakes import FakeBot, FakeOpenAIService, FakeTTSService, ObservedIsolation

# config.py calls load_dotenv() at import time. Stub it before anything imports `config`, so a developer's
# real .env (with live secrets) can never leak into the test process, and drop any ambient settings.
# (Hence nothing above this point, tests.fakes included, may import a module that imports `config`.)
dotenv.load_dotenv = lambda *args, **kwargs: False
for _name in (
    "BOT_TOKEN",
    "OPENAI_API_KEY",
    "SESSION_TIMEOUT_MINUTES",
    "OPENAI_TIMEOUT_SECONDS",
    "OPENAI_MAX_RETRIES",
    "OPENAI_TRANSCRIPTION_MODEL",
    "OPENAI_TRANSCRIPTION_LANGUAGE",
    "OPENAI_VISION_MODEL",
    "OPENAI_ANALYSIS_MODEL",
    "OPENAI_REPORT_MODEL",
    "OPENAI_TTS_MODEL",
    "OPENAI_TTS_VOICE",
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
    Telegram and OpenAI are both reached by hostname, so refusing DNS lookups and
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
    """handlers.document wired to fakes, with every session workspace the bot creates redirected into `temp_dir`."""

    bot: FakeBot
    temp_dir: Path
    document: object  # the handlers.document module
    ai: object  # services.openai_client.AIServices: what the handlers receive from aiogram's workflow_data; a test may swap `ai.openai` / `ai.tts`
    pipeline_calls: list[int] = field(default_factory=list)
    pipeline_args: list[tuple] = field(default_factory=list)  # (bot, user_id, voice_path, doc_path, ...) per run
    storage: MemoryStorage = field(default_factory=MemoryStorage)

    def new_state(self, user_id: int = 1) -> FSMContext:
        return FSMContext(storage=self.storage, key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))

    def leftover_files(self) -> list[str]:
        return sorted(p.name for p in self.temp_dir.iterdir())


@pytest.fixture
def handler_env(monkeypatch, tmp_path) -> HandlerEnv:
    import config
    from handlers import document
    from services.openai_client import AIServices

    # Session workspaces are created under tempfile.gettempdir(); point it at the per-test directory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(config, "SESSION_TIMEOUT_MINUTES", 10)

    env = HandlerEnv(
        bot=FakeBot(), temp_dir=tmp_path, document=document,
        ai=AIServices(openai=FakeOpenAIService(), tts=FakeTTSService()),
    )
    real_run_pipeline = document.run_pipeline

    async def counting_run_pipeline(*args, **kwargs):
        env.pipeline_calls.append(1)
        env.pipeline_args.append(args)
        return await real_run_pipeline(*args, **kwargs)

    monkeypatch.setattr(document, "run_pipeline", counting_run_pipeline)
    return env


@dataclass
class DispatchEnv:
    """The real Dispatcher from bot.build_dispatcher() (FSM middleware + event isolation) in front of `env`'s handlers."""

    env: HandlerEnv
    dispatcher: object
    isolation: ObservedIsolation

    def feed(self, update) -> asyncio.Task:
        return asyncio.create_task(self.dispatcher.feed_update(self.env.bot, update))

    def state_of(self, user_id: int) -> FSMContext:
        key = StorageKey(bot_id=self.env.bot.id, chat_id=user_id, user_id=user_id)
        return FSMContext(storage=self.dispatcher.storage, key=key)


@pytest.fixture(scope="session")
def _app_dispatcher():
    # The module-level routers attach to a single parent, so the app's dispatcher can be built once per process.
    from bot import build_dispatcher
    from services.openai_client import AIServices

    # Placeholder services: the `dispatch` fixture swaps in each test's own via workflow_data.
    return build_dispatcher(AIServices(openai=FakeOpenAIService(), tts=FakeTTSService()))


@pytest.fixture
def dispatch(handler_env, _app_dispatcher, monkeypatch) -> DispatchEnv:
    # Fresh locks per test (an asyncio.Lock binds to the loop of its first contention), same class as the app's.
    isolation = ObservedIsolation(type(_app_dispatcher.fsm.events_isolation)())
    monkeypatch.setattr(_app_dispatcher.fsm, "events_isolation", isolation)
    monkeypatch.setitem(_app_dispatcher.workflow_data, "ai", handler_env.ai)

    # aiogram runs sync callbacks (the F.voice / F.document magic filters) via asyncio.to_thread. That is a real
    # thread hop, so how far a second update gets while the first is parked would depend on thread timing.
    # Running them inline leaves the test's own Events and the isolation lock as the only suspension points.
    async def run_inline(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", run_inline)
    yield DispatchEnv(env=handler_env, dispatcher=_app_dispatcher, isolation=isolation)
    _app_dispatcher.storage.storage.clear()
