from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select

from bot.config import Settings
from bot.content import ContentError, Course, load_course
from bot.db import DELETED_USER_ID, AIUsage, BudgetTopup, Payment, Progress, User, budget_balance, confirm_payment, refund_payment, utcnow
from bot.handlers.common import RuntimeState
from bot.platega import Platega, PlategaError

router = Router(name="admin")


def setup_admin_filter(settings: Settings) -> None:
    router.message.filter(F.from_user.id.in_(settings.admin_ids))


@router.message(Command("stats"))
async def cmd_stats(message: Message, db, course: Course) -> None:
    day_ago = utcnow() - timedelta(days=1)
    async with db() as session:
        users = await session.scalar(select(func.count()).select_from(User).where(User.id != DELETED_USER_ID))
        new_users = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= day_ago))
        passed = await session.scalar(select(func.count()).select_from(Progress).where(Progress.quiz_passed_at.is_not(None)))
        reviews_day = await session.scalar(select(func.count()).select_from(AIUsage).where(AIUsage.created_at >= day_ago))
        spent_day = await session.scalar(
            select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(AIUsage.created_at >= day_ago))
        spent_total = await session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)))
        balance = await budget_balance(session)
        sales = (await session.execute(
            select(Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "paid", Payment.provider != "grant").group_by(Payment.currency))).all()
    sales_line = ", ".join(f"{n} шт. на {total} {'⭐' if cur == 'XTR' else '₽'}" for cur, n, total in sales) or "пока нет"
    published = sum(1 for l in course.lessons.values() if l.published)
    await message.answer(
        "<b>Статистика</b>\n"
        f"Пользователи: {users} (+{new_users} за сутки)\n"
        f"Сданных тестов: {passed}\n"
        f"Уроков: {len(course.lessons)}, опубликовано {published}\n\n"
        f"Проверок ИИ за сутки: {reviews_day}, ${spent_day:.4f}\n"
        f"Потрачено всего: ${spent_total:.4f}\n"
        f"Расчётный остаток бюджета: ${balance:.2f}\n\n"
        f"Продажи пакетов: {sales_line}"
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
    """Начислить разборы вручную: /grant <user_id> <разборов>"""
    try:
        user_id, reviews = (int(x) for x in (command.args or "").split())
    except ValueError:
        await message.answer("Использование: /grant <user_id> <разборов>")
        return
    async with db() as session:
        user = await session.get(User, user_id)
        if user is None:
            await message.answer("Такого пользователя нет: он должен сначала нажать /start.")
            return
        payment = Payment(user_id=user_id, provider="grant", external_id=f"grant:{user_id}:{utcnow().timestamp()}",
                          reviews=reviews, amount=0, currency="RUB")
        session.add(payment)
        await session.commit()
        await confirm_payment(session, payment.id)
        await session.refresh(user)
    await message.answer(f"Пользователю {user_id} начислено разборов: {reviews}. Теперь у него {user.credits}.")

@router.message(Command("payments"))
async def cmd_payments(message: Message, command: CommandObject, db) -> None:
    """Платежи пользователя: /payments <user_id>"""
    if not (command.args or "").strip().isdigit():
        await message.answer("Использование: /payments <user_id>")
        return
    user_id = int(command.args)
    async with db() as session:
        user = await session.get(User, user_id)
        rows = (await session.scalars(
            select(Payment).where(Payment.user_id == user_id, Payment.status.in_(("paid", "refunded")))
            .order_by(Payment.id))).all()
    if user is None or not rows:
        await message.answer("Оплаченных платежей у этого пользователя нет.")
        return
    lines = [f"<b>Платежи пользователя {user_id}</b>, на балансе разборов: {user.credits}", ""]
    lines += [f"#{p.id} · {p.paid_at:%d.%m.%Y} · {p.provider} · {p.reviews} разб. за {p.amount} {_sign(p)} · {p.status}"
              for p in rows]
    lines += ["", "Возврат: /refund <номер платежа>"]
    await message.answer("\n".join(lines))


def _sign(payment: Payment) -> str:
    return "⭐" if payment.currency == "XTR" else "₽"


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot, db, platega: Platega | None) -> None:
    """Возврат платежа: /refund <номер> - целиком через кассу или Telegram; /refund <номер> manual - только учёт."""
    args = (command.args or "").split()
    if not args or not args[0].isdigit() or args[1:] not in ([], ["manual"]):
        await message.answer("Использование: /refund <номер платежа> [manual]. Номера - в /payments <user_id>.")
        return
    manual = len(args) == 2
    async with db() as session:
        payment = await session.get(Payment, int(args[0]))
        user = await session.get(User, payment.user_id) if payment else None
    if payment is None or payment.status != "paid" or payment.provider == "grant":
        await message.answer("Такого оплаченного платежа нет (или он уже возвращён).")
        return

    unused = min(user.credits, payment.reviews)
    if not manual:
        if unused < payment.reviews:
            part = payment.amount * unused // payment.reviews
            await message.answer(
                f"Из пакета в {payment.reviews} разборов не использовано {unused}. По оферте к возврату "
                f"{part} {_sign(payment)} из {payment.amount}.\n\n"
                "Касса и Telegram возвращают платёж только целиком, поэтому частичный возврат сделай вручную "
                f"(в кабинете кассы или переводом), а потом отметь: /refund {payment.id} manual - "
                "оставшиеся разборы пакета спишутся.")
            return
        error = await _provider_refund(bot, platega, payment)
        if error:
            await message.answer(f"Возврат не прошёл: {error}\nБаланс пользователя не менялся.")
            return

    async with db() as session:
        taken = await refund_payment(session, payment.id)
    if taken is None:
        await message.answer("Платёж уже возвращён.")
        return
    how = "отмечен как возвращённый вручную" if manual else "возвращён"
    await message.answer(f"Платёж #{payment.id} {how}. Списано разборов: {taken}.")
    try:
        await bot.send_message(payment.user_id, f"Возврат оформлен: платёж на {payment.amount} {_sign(payment)} "
                                                f"от {payment.paid_at:%d.%m.%Y}. Разборов списано: {taken}.")
    except Exception:  # пользователь мог заблокировать бота
        pass


async def _provider_refund(bot: Bot, platega: Platega | None, payment: Payment) -> str | None:
    """Вернуть деньги через того, кто их принял. Возвращает текст ошибки или None при успехе."""
    try:
        if payment.provider == "stars":
            await bot.refund_star_payment(user_id=payment.user_id, telegram_payment_charge_id=payment.external_id)
            return None
        if platega is None:
            return "касса не подключена"
        supported, reason = await platega.cancel_supported(payment.external_id)
        if not supported:
            return f"касса не может отменить этот платёж ({reason or 'причина не указана'})"
        accepted, text = await platega.cancel(payment.external_id)
        return None if accepted else f"касса просит обратиться в поддержку ({text})"
    except (TelegramAPIError, PlategaError) as exc:
        return str(exc)


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
