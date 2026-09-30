"""Оформление бота в Telegram: описание, подпись в профиле и аватар.

Всё это можно задать и в @BotFather; здесь оно лежит рядом с кодом, чтобы текст не расходился с курсом.
"""
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputProfilePhotoStatic

log = logging.getLogger(__name__)

LOGO = Path(__file__).parent / "assets" / "logo.jpg"

# Показывается в пустом чате до нажатия "Запустить" (до 512 символов)
DESCRIPTION = (
    "Hop-by-Hop - курс по компьютерным сетям: от битов и Ethernet до TLS 1.3 и современных прокси-протоколов, "
    "с упором на безопасность.\n\n"
    "• 105 уроков по учебникам Олифер и Таненбаума, с опытами на настоящем Linux\n"
    "• тест после каждого урока\n"
    "• вопросы на понимание: ответ своими словами разбирает ИИ\n\n"
    "Уроки и тесты бесплатны. Платные только разборы ИИ, первые 15 - в подарок."
)
# Показывается в профиле бота и при пересылке ссылки на него (до 120 символов)
SHORT_DESCRIPTION = "Курс по компьютерным сетям: уроки, тесты и разбор ответов ИИ. Уроки и тесты бесплатны."


async def setup_profile(bot: Bot) -> None:
    """Привести оформление к тексту выше. Меняет только то, что отличается: у этих методов строгие лимиты."""
    try:
        if (await bot.get_my_description()).description != DESCRIPTION:
            await bot.set_my_description(DESCRIPTION)
        if (await bot.get_my_short_description()).short_description != SHORT_DESCRIPTION:
            await bot.set_my_short_description(SHORT_DESCRIPTION)
        me = await bot.me()
        if LOGO.exists() and (await bot.get_user_profile_photos(me.id, limit=1)).total_count == 0:
            await bot.set_my_profile_photo(InputProfilePhotoStatic(photo=FSInputFile(LOGO)))
    except TelegramAPIError:  # оформление не должно мешать запуску
        log.warning("Не удалось обновить оформление бота", exc_info=True)
