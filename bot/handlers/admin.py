from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select

from bot.config import Settings
from bot.content import ContentError, Course, load_course
from bot.db import AIUsage, BudgetTopup, Progress, User, budget_balance, utcnow
from bot.handlers.common import RuntimeState

router = Router(name="admin")


def setup_admin_filter(settings: Settings) -> None:
    router.message.filter(F.from_user.id.in_(settings.admin_ids))


@router.message(Command("stats"))
async def cmd_stats(message: Message, db, course: Course) -> None:
    day_ago = utcnow() - timedelta(days=1)
    async with db() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        new_users = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= day_ago))
        passed = await session.scalar(select(func.count()).select_from(Progress).where(Progress.quiz_passed_at.is_not(None)))
        reviews_day = await session.scalar(select(func.count()).select_from(AIUsage).where(AIUsage.created_at >= day_ago))
        spent_day = await session.scalar(
            select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(AIUsage.created_at >= day_ago))
        spent_total = await session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)))
        balance = await budget_balance(session)
    published = sum(1 for l in course.lessons.values() if l.published)
    await message.answer(
        "<b>Статистика</b>\n"
        f"Пользователи: {users} (+{new_users} за сутки)\n"
        f"Сданных тестов: {passed}\n"
        f"Уроков: {len(course.lessons)}, опубликовано {published}\n\n"
        f"Проверок ИИ за сутки: {reviews_day}, ${spent_day:.4f}\n"
        f"Потрачено всего: ${spent_total:.4f}\n"
        f"Расчётный остаток бюджета: ${balance:.2f}"
    )


@router.message(Command("topup"))
async def cmd_topup(message: Message, command: CommandObject, db, runtime: RuntimeState) -> None:
    """Отметить пополнение баланса Claude API: /topup 20"""
    try:
        amount = float((command.args or "").replace(",", "."))
    except ValueError:
        amount = 0
    if amount <= 0:
        await message.answer("Использование: /topup 20 (сумма в $, которую ты внесла в консоли Anthropic)")
        return
    async with db() as session:
        session.add(BudgetTopup(amount_usd=amount))
        await session.commit()
        balance = await budget_balance(session)
    runtime.low_budget_alerted = runtime.empty_budget_alerted = False
    await message.answer(f"Записала пополнение ${amount:.2f}. Расчётный остаток: ${balance:.2f}")


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject, db) -> None:
    """Выдать платный тариф вручную: /grant <user_id> <дней>"""
    try:
        user_id, days = (int(x) for x in (command.args or "").split())
    except ValueError:
        await message.answer("Использование: /grant <user_id> <дней>")
        return
    async with db() as session:
        user = await session.get(User, user_id)
        if user is None:
            await message.answer("Такого пользователя нет: он должен сначала нажать /start.")
            return
        user.paid_until = utcnow() + timedelta(days=days)
        await session.commit()
    await message.answer(f"Пользователю {user_id} выдан платный тариф на {days} дн.")


@router.message(Command("reload"))
async def cmd_reload(message: Message, course: Course) -> None:
    """Перечитать уроки с диска без перезапуска бота."""
    try:
        fresh = load_course()
    except ContentError as exc:
        await message.answer(f"Контент не загружен, осталась старая версия.\nОшибка: {exc}")
        return
    course.lessons = fresh.lessons
    published = sum(1 for l in course.lessons.values() if l.published)
    await message.answer(f"Контент перечитан: {len(course.lessons)} уроков, опубликовано {published}.")
