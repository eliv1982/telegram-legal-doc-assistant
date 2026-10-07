"""
Regression ledger: FSM concurrency defect (fixed).

aiogram runs each update as its own task. Without events isolation a voice update and a document update from
one user interleaved and overwrote each other's state. These tests go through the Dispatcher built by
bot.build_dispatcher(), because the isolation lives there, not in the handlers. The interleaving is forced
with asyncio.Events, not with sleeps, so it is the same on every run.
"""
import asyncio

from aiogram.fsm.storage.memory import SimpleEventIsolation

from tests.fakes import Gate, document_update, voice_update

# A bounded wait only turns a deadlock (e.g. a global instead of a per-user lock) into a failure instead of a hung CI job.
DEADLOCK_GUARD_SECONDS = 5


def test_app_dispatcher_serializes_events_per_user(_app_dispatcher):
    assert isinstance(_app_dispatcher.fsm.events_isolation, SimpleEventIsolation)


async def test_document_and_voice_arriving_together_trigger_exactly_one_pipeline(dispatch):
    """
    A voice and a document that both arrive start exactly one pipeline run. The voice handler is parked
    mid-download; the document update then reaches the isolation boundary. It must wait there instead of
    running on a stale view of the state and having its state overwritten when the voice handler resumes.
    """
    env = dispatch.env
    voice_download = Gate()

    async def hold_voice_download(file_path: str) -> None:
        if "voice" in file_path:
            await voice_download.hold()

    env.bot.before_download = hold_voice_download

    voice_task = dispatch.feed(voice_update(1, user_id=1001))
    await voice_download.reached.wait()  # voice handler has read the (empty) state and is mid-download
    document_task = dispatch.feed(document_update(2, user_id=1001))
    await dispatch.isolation.requests.get()  # the voice update's own lock request
    await dispatch.isolation.requests.get()  # the document update has now reached the isolation boundary
    voice_download.release.set()
    await asyncio.gather(voice_task, document_task)

    assert len(env.pipeline_calls) == 1
    assert env.leftover_files() == []
    assert await dispatch.state_of(1001).get_state() is None


async def test_one_users_slow_download_does_not_block_another_user(dispatch):
    """Users are independent: while A's voice is stuck downloading, B completes a whole session, then A does too."""
    env = dispatch.env
    voice_a_download = Gate()

    async def hold_voice_a_download(file_path: str) -> None:
        if file_path == "remote/voice-a":
            await voice_a_download.hold()

    env.bot.before_download = hold_voice_a_download

    voice_a = dispatch.feed(voice_update(1, user_id=2001, unique_id="a"))
    await voice_a_download.reached.wait()

    for update in (voice_update(2, user_id=2002, unique_id="b"), document_update(3, user_id=2002, unique_id="b")):
        await asyncio.wait_for(dispatch.feed(update), DEADLOCK_GUARD_SECONDS)
    assert [args[1] for args in env.pipeline_args] == [2002]  # B's session ran with only B's files

    voice_a_download.release.set()
    await voice_a
    assert await dispatch.state_of(2001).get_state() == "UserSessionState:waiting_for_document"
    await asyncio.wait_for(dispatch.feed(document_update(4, user_id=2001, unique_id="a")), DEADLOCK_GUARD_SECONDS)

    assert [args[1] for args in env.pipeline_args] == [2002, 2001]
    assert env.leftover_files() == []
