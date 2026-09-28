import logging
from dataclasses import dataclass, field
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.access import Tier, tier_of
from bot.config import Settings
from bot.content import Course
from bot.db import User

log = logging.getLogger(__name__)


@dataclass
class RuntimeState:
    """Состояние процесса, которое не нужно хранить в базе."""

    low_budget_alerted: bool = False
    empty_budget_alerted: bool = False
    extra: dict = field(default_factory=dict)


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def sees_drafts(user: User, settings: Settings) -> bool:
    return tier_of(user, settings) is Tier.ADMIN


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def btn(text: str, data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, url=url)


def lesson_title(course: Course, lesson_id: int) -> str:
    lesson = course.lessons.get(lesson_id)
    return f"Урок {lesson_id}. {escape(lesson.title)}" if lesson else f"Урок {lesson_id}"


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # админ мог не запускать бота; не роняем обработку
            log.exception("Не удалось уведомить админа %s", admin_id)
