"""
Regression ledger: confirmed temp-file lifecycle defect (Stage 2), plus a passing control.

The control shows the leftover-file check and the whole fake pipeline work; the xfail shows the one
ordering where an input file is never removed.
"""
import pytest

from tests.fakes import FakeMessage


async def test_voice_first_then_document_leaves_no_input_files(handler_env):
    """Control (passes today): this ordering already cleans up both downloads."""
    env = handler_env
    state = env.new_state()

    await env.document.handle_voice(FakeMessage.with_voice(), state, env.bot)
    await env.document.handle_document(FakeMessage.with_document(), state, env.bot)

    assert env.pipeline_calls == [1]
    assert len(env.bot.sent_voices) == 1  # the pipeline ran to the end
    assert env.leftover_files() == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Stage 2: confirmed file/session lifecycle defect",
)
async def test_document_first_then_voice_leaves_no_input_files(handler_env):
    """
    Desired: after a successful run nothing the user uploaded stays on disk. Today the document-first
    branch of handle_voice unlinks the document but never the voice_<user>_<id>.ogg it just downloaded.
    """
    env = handler_env
    state = env.new_state()

    await env.document.handle_document(FakeMessage.with_document(), state, env.bot)
    await env.document.handle_voice(FakeMessage.with_voice(), state, env.bot)

    if env.pipeline_calls != [1] or len(env.bot.sent_voices) != 1:  # harness check, not the defect
        pytest.fail("the fake pipeline did not run to completion")
    assert env.leftover_files() == []
