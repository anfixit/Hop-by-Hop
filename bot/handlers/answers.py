"""Открытые вопросы: ученик отвечает текстом, ответ разбирает Claude."""
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.access import Tier, model_for, tier_of, trial_left
from bot.config import Settings
from bot.content import Course
from bot.db import AIUsage, User, budget_balance, passed_lessons, refund_review, spend_review, upsert_user
from bot.handlers.common import RuntimeState, btn, kb, notify_admins, sees_drafts
from bot.handlers.payments import balance_line
from bot.review import MAX_ANSWER_CHARS, Reviewer, ReviewError

log = logging.getLogger(__name__)
router = Router(name="answers")

NO_REVIEWS = "Разборы закончились. Уроки и тесты по-прежнему бесплатны, а разборы ИИ можно докупить: /buy"
LOW_BALANCE = 3  # с этого остатка предупреждаем и показываем кнопку покупки
VERDICT_LABEL = {"correct": "✅ Верно", "partial": "🟡 Частично верно", "incorrect": "❌ Неверно"}


class Answering(StatesGroup):
    waiting = State()


async def _passed_lesson(db, settings, course, tg_user, lesson_id):
    async with db() as session:
        user = await upsert_user(session, tg_user)
        passed = await passed_lessons(session, user.id)
    lesson = course.get(lesson_id, sees_drafts(user, settings))
    return user, (lesson if lesson and lesson.id in passed else None)


@router.callback_query(F.data.regexp(r"^oq:\d+$"))
async def cb_questions(call: CallbackQuery, db, settings: Settings, course: Course) -> None:
    _, lesson = await _passed_lesson(db, settings, course, call.from_user, int(call.data.split(":")[1]))
    if lesson is None:
        await call.answer("Сначала сдай тест этого урока.", show_alert=True)
        return
    lines = [f"<b>Вопросы на понимание · урок {lesson.id}</b>", ""]
    lines += [f"{i + 1}. {escape(q.question)}" for i, q in enumerate(lesson.open_questions)]
    lines += ["", "Выбери вопрос и ответь своими словами. Ответ разберёт ИИ и укажет, где модель в голове неточна."]
    rows = [[btn(f"Ответить на {i + 1}", f"oq:{lesson.id}:{i}")] for i in range(len(lesson.open_questions))]
    rows.append([btn("← К уроку", f"ls:{lesson.id}")])
    await call.message.edit_text("\n".join(lines), reply_markup=kb(*rows))
    await call.answer()


@router.callback_query(F.data.regexp(r"^oq:\d+:\d+$"))
async def cb_pick_question(call: CallbackQuery, state: FSMContext, db, settings: Settings, course: Course,
                           reviewer: Reviewer | None) -> None:
    _, lesson_id, idx = call.data.split(":")
    user, lesson = await _passed_lesson(db, settings, course, call.from_user, int(lesson_id))
    idx = int(idx)
    if lesson is None or idx >= len(lesson.open_questions):
        await call.answer("Вопрос недоступен.", show_alert=True)
        return
    if reviewer is None:
        await call.answer("Проверка ответов пока не подключена.", show_alert=True)
        return
    if (refusal := await _check_limits(db, settings, user)) is not None:
        await call.answer(refusal, show_alert=True)
        if refusal == NO_REVIEWS:
            await call.message.answer(NO_REVIEWS, reply_markup=kb([btn("Купить разборы", "buy")]))
        return
    await state.set_state(Answering.waiting)
    await state.update_data(lesson_id=lesson.id, idx=idx)
    await call.message.answer(
        f"<b>Вопрос {idx + 1}.</b> {escape(lesson.open_questions[idx].question)}\n\n"
        f"Отправь ответ одним сообщением (до {MAX_ANSWER_CHARS} символов). /cancel - отмена."
    )
    await call.answer()


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. /lessons - оглавление.")


@router.message(Answering.waiting, F.text)
async def on_answer(message: Message, state: FSMContext, bot: Bot, db, settings: Settings, course: Course,
                    reviewer: Reviewer, runtime: RuntimeState) -> None:
    answer = message.text.strip()
    if len(answer) > MAX_ANSWER_CHARS:
        await message.answer(f"Слишком длинно: {len(answer)} символов. Сократи до {MAX_ANSWER_CHARS}.")
        return
    data = await state.get_data()
    user, lesson = await _passed_lesson(db, settings, course, message.from_user, data["lesson_id"])
    if lesson is None:
        await state.clear()
        await message.answer("Урок недоступен.")
        return
    if (refusal := await _check_limits(db, settings, user)) is not None:
        await state.clear()
        await message.answer(refusal, reply_markup=kb([btn("Купить разборы", "buy")]) if refusal == NO_REVIEWS else None)
        return
    await state.clear()

    # Разбор списываем до запроса к модели, чтобы два одновременных ответа не прошли по одному разбору
    if tier_of(user, settings) is Tier.ADMIN:
        kind = "admin"
    else:
        async with db() as session:
            kind = await spend_review(session, user.id, settings.trial_reviews)
        if kind is None:
            await message.answer(NO_REVIEWS, reply_markup=kb([btn("Купить разборы", "buy")]))
            return

    question = lesson.open_questions[data["idx"]]
    pending = await message.answer("Проверяю ответ...")
    try:
        result = await reviewer.review(model_for(kind, settings), lesson, question, answer)
    except ReviewError as exc:
        if exc.usage:
            await _record_usage(db, user.id, lesson.id, *exc.usage)
        if kind != "admin":  # проверка не состоялась - разбор возвращаем
            async with db() as session:
                await refund_review(session, user.id, kind)
        await pending.edit_text(str(exc))
        return
    await _record_usage(db, user.id, lesson.id, result.model, result.input_tokens, result.output_tokens,
                        result.cost_usd)

    v = result.verdict
    parts = [f"<b>{VERDICT_LABEL[v.verdict]}</b>", "", escape(v.feedback)]
    if v.misconceptions:
        parts += ["", "<b>Ошибки в понимании:</b>"] + [f"• {escape(m)}" for m in v.misconceptions]
    if v.missing_points:
        parts += ["", "<b>Не хватает:</b>"] + [f"• {escape(m)}" for m in v.missing_points]
    if kind != "admin":
        async with db() as session:
            fresh = await session.get(User, user.id)
        parts += ["", f"<i>{balance_line(fresh, settings)}</i>"]
        left = fresh.credits + trial_left(fresh, settings)
    rows = [[btn("Ответить ещё раз", f"oq:{lesson.id}:{data['idx']}")], [btn("← К вопросам", f"oq:{lesson.id}")]]
    if kind != "admin" and left <= LOW_BALANCE:
        parts += ["", "⚠️ Это был последний разбор. Уроки и тесты остаются бесплатными, разборы можно докупить."
                  if left == 0 else "⚠️ Разборы заканчиваются. Пакет можно купить заранее, он не сгорает."]
        rows.insert(0, [btn("Купить разборы", "buy")])
    await pending.edit_text("\n".join(parts), reply_markup=kb(*rows))
    await _budget_alerts(bot, db, settings, runtime)

@router.message(Answering.waiting)
async def on_non_text(message: Message) -> None:
    await message.answer("Жду ответ текстом. /cancel - отмена.")


async def _check_limits(db, settings: Settings, user) -> str | None:
    if tier_of(user, settings) is Tier.ADMIN:
        return None
    async with db() as session:
        if await budget_balance(session) <= 0:
            return "Проверка ответов временно недоступна. Тесты и уроки работают как обычно."
    if user.credits <= 0 and trial_left(user, settings) <= 0:
        return NO_REVIEWS
    return None

async def _record_usage(db, user_id, lesson_id, model, input_tokens, output_tokens, cost_usd) -> None:
    async with db() as session:
        session.add(AIUsage(user_id=user_id, lesson_id=lesson_id, model=model, input_tokens=input_tokens,
                            output_tokens=output_tokens, cost_usd=cost_usd))
        await session.commit()


async def _budget_alerts(bot: Bot, db, settings: Settings, runtime: RuntimeState) -> None:
    async with db() as session:
        balance = await budget_balance(session)
    if balance <= 0 and not runtime.empty_budget_alerted:
        runtime.empty_budget_alerted = True
        await notify_admins(bot, settings, f"⛔ Бюджет Claude API исчерпан (расчётный остаток ${balance:.2f}). "
                                           "Проверка для учеников остановлена. Пополни баланс и отметь /topup.")
    elif balance < settings.budget_alert_usd and not runtime.low_budget_alerted:
        runtime.low_budget_alerted = True
        await notify_admins(bot, settings, f"⚠️ Расчётный остаток бюджета Claude API: ${balance:.2f}. "
                                           "Пора пополнить и отметить /topup.")
