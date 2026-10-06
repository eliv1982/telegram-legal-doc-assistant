"""
Regression ledger: confirmed FSM concurrency defect (Stage 2).

aiogram runs each update as its own task and the Dispatcher here has no events isolation, so a voice
update and a document update from one user can interleave. The interleaving below is forced with an
asyncio.Event, not with sleeps or timing, so it is the same on every run.
"""
import asyncio

import pytest

from tests.fakes import FakeMessage, Gate


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Stage 2: confirmed FSM concurrency defect (no events isolation)",
)
async def test_document_and_voice_arriving_together_trigger_exactly_one_pipeline(handler_env):
    """
    Desired: a voice and a document that both arrive start exactly one pipeline run.
    Today handle_voice reads the FSM state *before* its download. The document is handled while that
    download is in flight, then the voice handler resumes on its stale "no state" view and overwrites
    the state the document just set. Neither side ever sees the other: zero pipelines, session stuck.
    """
    env = handler_env
    state = env.new_state()
    voice_download = Gate()

    async def hold_voice_download(file_path: str) -> None:
        if "voice" in file_path:
            await voice_download.hold()

    env.bot.before_download = hold_voice_download

    voice_task = asyncio.create_task(env.document.handle_voice(FakeMessage.with_voice(), state, env.bot))
    await voice_download.reached.wait()  # voice handler has read the (empty) state and is mid-download
    await env.document.handle_document(FakeMessage.with_document(), state, env.bot)
    voice_download.release.set()
    await voice_task

    assert len(env.pipeline_calls) == 1
