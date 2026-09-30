"""Запуск: python -m bot"""
import asyncio
import hashlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import Settings
from bot.content import load_course
from bot.db import init_db
from bot.handlers import admin, answers, lessons, payments, quiz
from bot.handlers.common import RuntimeState
from bot.platega import Platega
from bot.review import Reviewer


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    course = load_course()
    engine, db = await init_db(settings)
    reviewer = (
        Reviewer(settings.anthropic_api_key.get_secret_value(), settings.proxy_url)
        if settings.anthropic_api_key else None
    )
    if reviewer is None:
        logging.warning("ANTHROPIC_API_KEY не задан: проверка открытых ответов отключена")

    platega = (
        Platega(settings.platega_merchant_id, settings.platega_secret.get_secret_value(), settings.proxy_url,
                settings.platega_payment_method)
        if settings.platega_enabled else None
    )
    if platega is None:
        logging.warning("Ключи Platega не заданы: оплата только звёздами Telegram")

    token = settings.bot_token.get_secret_value()
    session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher(
        storage=MemoryStorage(),
        settings=settings,
        course=course,
        db=db,
        reviewer=reviewer,
        platega=platega,
        runtime=RuntimeState(),
        # ключ подписи callback-данных выводится из токена бота
        secret=hashlib.sha256(b"hop-by-hop/callbacks|" + token.encode()).digest(),
    )
    admin.setup_admin_filter(settings)
    dp.include_routers(admin.router, payments.router, answers.router, quiz.router, lessons.router)

    await bot.set_my_commands([
        BotCommand(command="lessons", description="Оглавление курса"),
        BotCommand(command="buy", description="Разборы ИИ: остаток и покупка"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="cancel", description="Отменить ввод ответа"),
        BotCommand(command="terms", description="Оферта и документы"),
    ])
    logging.info("Уроков загружено: %d", len(course.lessons))
    poller = asyncio.create_task(payments.poll_platega(bot, db, settings, platega)) if platega else None
    try:
        await dp.start_polling(bot)
    finally:
        if poller:
            poller.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
