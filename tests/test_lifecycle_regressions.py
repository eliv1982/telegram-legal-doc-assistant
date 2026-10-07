"""
Regression ledger: temp-file lifecycle defect (fixed), plus a passing control.

The control shows the leftover-file check and the whole fake pipeline work; the second test is the one
ordering where an input file used to be left behind (it was a strict xfail until the session workspace
took over ownership of every download).
"""
import pytest

from tests.fakes import FakeMessage


async def test_voice_first_then_document_leaves_no_input_files(handler_env):
    """Control: this ordering always cleaned up both downloads."""
    env = handler_env
    state = env.new_state()

    await env.document.handle_voice(FakeMessage.with_voice(), state, env.bot, env.ai)
    await env.document.handle_document(FakeMessage.with_document(), state, env.bot, env.ai)

    assert env.pipeline_calls == [1]
    assert len(env.bot.sent_voices) == 1  # the pipeline ran to the end
    assert env.leftover_files() == []


async def test_document_first_then_voice_leaves_no_input_files(handler_env):
    """
    After a successful run nothing the user uploaded stays on disk. The document-first branch of
    handle_voice used to unlink the document but never the voice file it had just downloaded.
    """
    env = handler_env
    state = env.new_state()

    await env.document.handle_document(FakeMessage.with_document(), state, env.bot, env.ai)
    await env.document.handle_voice(FakeMessage.with_voice(), state, env.bot, env.ai)

    if env.pipeline_calls != [1] or len(env.bot.sent_voices) != 1:  # harness check, not the defect
        pytest.fail("the fake pipeline did not run to completion")
    assert env.leftover_files() == []
