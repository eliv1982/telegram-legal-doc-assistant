"""
FSM-состояния для связывания голосового сообщения и документа в одну сессию.

Пока сессия ждёт вторую половину, FSM-данные владеют её рабочей директорией
(services.workspace.SessionWorkspace): путь к ней, путь к уже полученному файлу и время начала.
"""
import time
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
from services.workspace import SessionWorkspace

WORKSPACE_KEY = "workspace"
PENDING_FILE_KEY = "pending_file"
STARTED_AT_KEY = "started_at"


class UserSessionState(StatesGroup):
    """Состояния сессии пользователя."""

    waiting_for_document = State()  # после голоса — ожидаем документ
    waiting_for_voice = State()  # после документа — ожидаем голос


def session_expired(data: dict) -> bool:
    """Ленивая проверка таймаута: сессия, начатая SESSION_TIMEOUT_MINUTES минут назад, считается истёкшей."""
    started_at = data.get(STARTED_AT_KEY)
    return started_at is not None and time.time() - started_at >= config.SESSION_TIMEOUT_MINUTES * 60


async def discard_session(state: FSMContext) -> None:
    """Сбрасывает FSM и удаляет рабочую директорию, которой владела сессия. Можно вызывать повторно."""
    data = await state.get_data()
    await state.clear()
    workspace = data.get(WORKSPACE_KEY)
    if workspace:
        SessionWorkspace(Path(workspace)).cleanup()
