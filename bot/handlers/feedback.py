"""Сообщения об ошибках в уроках: кнопка в уроке или /report, текст уходит админам."""
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.content import Course
from bot.handlers.common import lesson_title

log = logging.getLogger(__name__)
router = Router(name="feedback")


class Reporting(StatesGroup):
    waiting = State()


PROMPT = ("Опиши одним сообщением, что не так: опечатка, ошибка в тексте или тесте, команда не работает. "
          "Можно приложить скриншот с подписью. /cancel - отмена.")


@router.callback_query(F.data.regexp(r"^rp:\d+$"))
async def cb_report(call: CallbackQuery, state: FSMContext, course: Course) -> None:
    lesson_id = int(call.data.split(":")[1])
    await state.set_state(Reporting.waiting)
    await state.update_data(lesson_id=lesson_id)
    await call.message.answer(f"<b>Сообщение об ошибке · {lesson_title(course, lesson_id)}</b>\n\n{PROMPT}")
    await call.answer()


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    await state.set_state(Reporting.waiting)
    await state.update_data(lesson_id=0)
    await message.answer(f"<b>Сообщение об ошибке</b>\n\n{PROMPT}")


# Команды (в том числе /cancel) пропускаем дальше, к своим обработчикам
@router.message(Reporting.waiting, ~F.text.startswith("/"))
async def on_report(message: Message, state: FSMContext, bot: Bot, settings: Settings, course: Course) -> None:
    lesson_id = (await state.get_data()).get("lesson_id", 0)
    await state.clear()
    where = lesson_title(course, lesson_id) if lesson_id else "без урока"
    user = message.from_user
    who = f"@{user.username}" if user.username else (user.first_name or "без имени")
    head = f"✉️ <b>Сообщение об ошибке</b> · {where}\nОт: {escape(who)} (id {user.id})"
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, head)
            await message.copy_to(admin_id)  # копия, а не пересылка: доходит и текст, и скриншот
        except Exception:
            log.exception("Не удалось передать сообщение об ошибке админу %s", admin_id)
    if settings.report_email_enabled:
        body = f"{where}\nОт: {who} (id {user.id})\n\n{message.text or message.caption or '(вложение без текста, см. Telegram)'}"
        asyncio.create_task(_send_email(settings, f"Hop-by-Hop: ошибка, {where}", body))
    await message.answer("Спасибо! Передал автору курса.")


async def _send_email(settings: Settings, subject: str, body: str) -> None:
    try:
        await asyncio.to_thread(_smtp_send, settings, subject, body)
    except Exception:
        log.exception("Не удалось отправить письмо об ошибке в уроке")


def _smtp_send(settings: Settings, subject: str, body: str) -> None:
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = settings.smtp_user, settings.report_email, subject
    mail.set_content(body)
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        smtp.send_message(mail)
