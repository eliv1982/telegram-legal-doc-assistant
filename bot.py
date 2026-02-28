"""
Основной файл запуска Telegram-бота.
Мультимодальный ассистент: голос + документ → отчёт, голосовое резюме, чек-лист.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
from handlers.start import router as start_router
from handlers.document import router as document_router
from utils.logging_config import setup_logging

setup_logging(log_level="INFO")
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env с BOT_TOKEN=...")
        sys.exit(1)
    if not config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не задан. Создайте файл .env с OPENAI_API_KEY=...")
        sys.exit(1)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(start_router)
    dp.include_router(document_router)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
