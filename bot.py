"""
Основной файл запуска Telegram-бота.
Мультимодальный ассистент: голос + документ → отчёт, голосовое резюме, чек-лист.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

import config
from handlers.start import router as start_router
from handlers.document import router as document_router
from services.openai_client import AIServices, create_openai_client
from services.workspace import purge_stale_workspaces
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def build_dispatcher(ai: AIServices) -> Dispatcher:
    """
    События одного пользователя/чата обрабатываются строго по очереди (SimpleEventIsolation),
    поэтому голос и документ, пришедшие одновременно, не перетирают состояние друг друга;
    разные пользователи друг друга не блокируют. Роутеры можно подключить только к одному Dispatcher.
    Сервисы ИИ передаются хендлерам через workflow_data aiogram (параметр `ai`).
    """
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation(), ai=ai)
    dp.include_router(start_router)
    dp.include_router(document_router)
    return dp


def create_bot(token: str) -> Bot:
    """
    Все сообщения бота — обычный текст: режим разметки (Markdown/HTML) не включён нигде, поэтому текст модели и документа
    нельзя ни «сломать» подчёркиваниями, скобками и тегами, ни заставить создать ссылку или форматирование. Превью
    ссылок выключено: адрес в тексте модели не подгрузит карточку сайта. Сам Telegram может подсветить голый адрес как
    ссылку, но она ведёт ровно туда, что написано.
    """
    return Bot(token=token, default=DefaultBotProperties(link_preview_is_disabled=True))


async def main() -> None:
    setup_logging(log_level="INFO")
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env с BOT_TOKEN=...")
        sys.exit(1)
    if not config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не задан. Создайте файл .env с OPENAI_API_KEY=...")
        sys.exit(1)

    # FSM в памяти: рабочие директории упавшего процесса после перезапуска никому не принадлежат.
    # Удаляем те, что старше таймаута сессии (живая сессия не может быть старше).
    removed = purge_stale_workspaces(config.SESSION_TIMEOUT_MINUTES * 60)
    if removed:
        logger.info("Удалено устаревших рабочих директорий прошлого запуска: %d", removed)

    bot = create_bot(config.BOT_TOKEN)
    # Один клиент OpenAI на процесс: свои тайм-аут и число повторов, закрывается при остановке.
    openai_client = create_openai_client(config.OPENAI_API_KEY)
    dp = build_dispatcher(AIServices.around(openai_client))

    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await openai_client.close()


if __name__ == "__main__":
    asyncio.run(main())
