"""
FSM-состояния для связывания голосового сообщения и документа в одну сессию.
"""
from aiogram.fsm.state import State, StatesGroup


class UserSessionState(StatesGroup):
    """Состояния сессии пользователя."""

    waiting_for_document = State()  # после голоса — ожидаем документ
    waiting_for_voice = State()  # после документа — ожидаем голос
