"""
Session lifecycle (Stage 2): every interaction owns exactly one workspace, and replacement, /start, expiry
and every failure path remove it. Offline: handlers run against fakes, workspaces live under the per-test dir.
"""
import logging
import time
from pathlib import Path

import pytest

from handlers import start
from handlers.document import DOWNLOAD_ERROR_MSG, ERROR_MSG, SESSION_EXPIRED_MSG, WAIT_DOC_MSG
from services.report import REPORT_HEADER
from states.user_states import PENDING_FILE_KEY, STARTED_AT_KEY, WORKSPACE_KEY
from tests.fakes import FAKE_BOT_TOKEN, FakeMessage, FakeOpenAIService

WAITING_FOR_DOCUMENT = "UserSessionState:waiting_for_document"
WAITING_FOR_VOICE = "UserSessionState:waiting_for_voice"


async def send(env, kind: str, state, unique_id: str = "x", user_id: int = 1) -> FakeMessage:
    """Deliver a voice or a document message straight to its handler."""
    if kind == "voice":
        message, handler = FakeMessage.with_voice(unique_id, user_id), env.document.handle_voice
    else:
        message, handler = FakeMessage.with_document(unique_id=unique_id, user_id=user_id), env.document.handle_document
    await handler(message, state, env.bot, env.ai)
    return message


async def boom(*args, **kwargs):
    raise RuntimeError("simulated failure")


def refuse_the_report(env, monkeypatch) -> None:
    """Only the primary deliverable fails to go out (an optional file failing is tests/test_delivery.py)."""
    real_send = env.bot.send_message

    async def send_message(chat_id, text, **kwargs):
        if REPORT_HEADER in text:
            raise RuntimeError("simulated Telegram failure")
        return await real_send(chat_id, text, **kwargs)

    monkeypatch.setattr(env.bot, "send_message", send_message)


def write_partial_then_fail(message: str = "simulated failure"):
    """A download that leaves a partial file behind and then fails, like a dropped connection."""

    async def download_file(file_path, destination):
        Path(destination).write_bytes(b"partial")
        raise RuntimeError(message)

    return download_file


@pytest.mark.parametrize("kind", ["voice", "document"])
async def test_replacing_a_pending_file_removes_the_previous_workspace(handler_env, kind):
    env = handler_env
    state = env.new_state()
    await send(env, kind, state, "first")
    first = (await state.get_data())[WORKSPACE_KEY]

    await send(env, kind, state, "second")
    second = (await state.get_data())[WORKSPACE_KEY]

    assert first != second
    assert not Path(first).exists()
    assert env.leftover_files() == [Path(second).name]

    await send(env, "document" if kind == "voice" else "voice", state, "other")  # the session still completes
    assert env.pipeline_calls == [1]
    assert Path(env.pipeline_args[0][2]).parent == Path(second)  # ran on the replacement, not the first file
    assert env.leftover_files() == []


async def test_start_discards_the_pending_workspace_and_resets_state(handler_env):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    assert len(env.leftover_files()) == 1

    await start.cmd_start(FakeMessage(), state)
    await start.cmd_start(FakeMessage(), state)  # repeating the reset is harmless

    assert env.leftover_files() == []
    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.parametrize(
    "break_it, reply_channel",
    [
        pytest.param(lambda env, mp: mp.setattr(FakeOpenAIService, "transcribe_voice", boom), "bot", id="pipeline-exception"),
        pytest.param(lambda env, mp: refuse_the_report(env, mp), "bot", id="report-cannot-be-sent"),
        pytest.param(lambda env, mp: mp.setattr(env.bot, "send_message", boom), "message", id="error-report-also-fails"),
    ],
)
async def test_failures_still_remove_the_workspace(handler_env, monkeypatch, break_it, reply_channel):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    break_it(env, monkeypatch)

    message = await send(env, "voice", state)

    assert env.pipeline_calls == [1]
    assert env.leftover_files() == []
    assert await state.get_state() is None
    # The user is told even when the pipeline's own error report could not be delivered.
    assert ERROR_MSG in (env.bot.sent_texts if reply_channel == "bot" else message.answers)


async def test_missing_pending_file_resets_the_session(handler_env):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    Path((await state.get_data())[PENDING_FILE_KEY]).unlink()  # e.g. removed behind the bot's back

    message = await send(env, "voice", state)

    assert message.answers == [ERROR_MSG]
    assert env.pipeline_calls == []
    assert env.leftover_files() == []
    assert await state.get_state() is None


async def test_failed_first_download_leaves_nothing_behind_and_logs_no_url(handler_env, monkeypatch, caplog):
    """The real failure carries the file URL, which contains the bot token (aiohttp ClientResponseError)."""
    env = handler_env
    state = env.new_state()
    url = f"https://api.telegram.org/file/bot{FAKE_BOT_TOKEN}/voice/file_1.oga"
    monkeypatch.setattr(env.bot, "download_file", write_partial_then_fail(f"404, message='Not Found', url='{url}'"))
    caplog.set_level(logging.DEBUG)

    message = await send(env, "voice", state)

    assert message.answers == [DOWNLOAD_ERROR_MSG]
    assert env.leftover_files() == []
    assert await state.get_state() is None
    assert "RuntimeError" in caplog.text  # the exception class is logged ...
    assert FAKE_BOT_TOKEN not in caplog.text and "api.telegram.org" not in caplog.text  # ... its text is not


async def test_failed_download_of_the_second_half_keeps_the_pending_half(handler_env, monkeypatch):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    workspace = Path((await state.get_data())[WORKSPACE_KEY])
    real_download = env.bot.download_file
    monkeypatch.setattr(env.bot, "download_file", write_partial_then_fail())

    message = await send(env, "voice", state)

    assert message.answers == [DOWNLOAD_ERROR_MSG]
    assert await state.get_state() == WAITING_FOR_VOICE
    assert [p.name for p in workspace.iterdir()] == ["document.png"]  # the partial voice file is gone

    monkeypatch.setattr(env.bot, "download_file", real_download)  # the user simply sends the voice again
    await send(env, "voice", state)
    assert env.pipeline_calls == [1]
    assert env.leftover_files() == []


async def test_expired_pending_session_is_dropped_and_a_new_one_starts(handler_env):
    env = handler_env
    state = env.new_state()
    await send(env, "document", state)
    stale = Path((await state.get_data())[WORKSPACE_KEY])
    await state.update_data({STARTED_AT_KEY: time.time() - 11 * 60})  # the timeout is 10 minutes

    message = await send(env, "voice", state)

    assert message.answers == [SESSION_EXPIRED_MSG, WAIT_DOC_MSG]
    assert not stale.exists()
    assert env.pipeline_calls == []  # the stale document was not paired with the new voice
    assert await state.get_state() == WAITING_FOR_DOCUMENT
    assert env.leftover_files() == [Path((await state.get_data())[WORKSPACE_KEY]).name]


async def test_two_users_keep_separate_sessions(handler_env):
    env = handler_env
    state_1, state_2 = env.new_state(1), env.new_state(2)

    await send(env, "document", state_1, "a", user_id=1)
    await send(env, "voice", state_2, "b", user_id=2)
    workspace_1 = Path((await state_1.get_data())[WORKSPACE_KEY])
    workspace_2 = Path((await state_2.get_data())[WORKSPACE_KEY])
    assert workspace_1 != workspace_2

    await send(env, "voice", state_1, "a", user_id=1)  # completes user 1 only
    assert [(args[1], Path(args[2]).parent) for args in env.pipeline_args] == [(1, workspace_1)]
    assert await state_2.get_state() == WAITING_FOR_DOCUMENT
    assert env.leftover_files() == [workspace_2.name]  # user 2's pending voice was not touched

    await send(env, "document", state_2, "b", user_id=2)
    assert [args[1] for args in env.pipeline_args] == [1, 2]
    assert env.leftover_files() == []
