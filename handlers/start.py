"""
Обработчик команды /start.
"""
import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from states.user_states import UserSessionState

router = Router()
logger = logging.getLogger(__name__)

WELCOME_TEXT = """
👋 Привет! Я — мультимодальный ассистент для анализа документов.

Отправь мне:
1️⃣ **Голосовое сообщение** с задачей (например: «Проверь этот договор на риски»)
2️⃣ **Документ** — PDF или изображение (JPG, PNG)

Можно в любом порядке. Я свяжу их в одну сессию и проанализирую.

📤 Что ты получишь:
• Текстовый отчёт (Markdown)
• Голосовое резюме
• Чек-лист в виде файла (PDF или изображение)

Отправь голос или документ, чтобы начать.
"""


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Обработка /start."""
    await state.clear()
    await message.answer(WELCOME_TEXT, parse_mode="Markdown")
    logger.info("User %s started bot", message.from_user.id if message.from_user else "?")
