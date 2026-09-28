"""Тест по уроку. Состояние (вопрос, счёт) едет в подписанных callback-данных."""
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.access import is_unlocked, sign, verify
from bot.config import Settings
from bot.content import Course
from bot.db import passed_lessons, record_quiz, upsert_user
from bot.handlers.common import btn, kb, sees_drafts

router = Router(name="quiz")

LETTERS = "АБВГДЕ"


@router.callback_query(F.data.regexp(r"^q[zar]:"))
async def cb_quiz(call: CallbackQuery, db, settings: Settings, course: Course, secret: bytes) -> None:
    payload = verify(secret, call.from_user.id, call.data)
    if payload is None:
        await call.answer("Кнопка устарела. Открой урок заново.", show_alert=True)
        return
    kind, lesson_id, *rest = payload.split(":")
    lesson_id = int(lesson_id)

    async with db() as session:
        user = await upsert_user(session, call.from_user)
        passed = await passed_lessons(session, user.id)
        drafts = sees_drafts(user, settings)
        lesson = course.get(lesson_id, drafts)
        if lesson is None or not is_unlocked(lesson, course, passed, drafts):
            await call.answer("Урок недоступен.", show_alert=True)
            return

        if kind == "qz":
            await show_question(call, lesson, int(rest[0]), int(rest[1]), user.id, secret)
        elif kind == "qa":
            await show_feedback(call, lesson, int(rest[0]), int(rest[1]), int(rest[2]), user.id, secret)
        else:  # qr - итог
            score = int(rest[0])
            total = len(lesson.quiz)
            ok = score >= settings.quiz_pass_ratio * total
            await record_quiz(session, user.id, lesson.id, score, ok)
            await show_result(call, lesson, score, ok, course, drafts, user.id, secret)
    await call.answer()


async def show_question(call, lesson, idx, score, user_id, secret):
    q = lesson.quiz[idx]
    lines = [f"<b>Тест к уроку {lesson.id}</b> · вопрос {idx + 1} из {len(lesson.quiz)}", "", escape(q.question), ""]
    lines += [f"{LETTERS[i]}) {escape(o)}" for i, o in enumerate(q.options)]
    buttons = [btn(LETTERS[i], sign(secret, user_id, f"qa:{lesson.id}:{idx}:{score}:{i}")) for i in range(len(q.options))]
    await call.message.edit_text("\n".join(lines), reply_markup=kb(buttons))


async def show_feedback(call, lesson, idx, score, choice, user_id, secret):
    q = lesson.quiz[idx]
    right = choice == q.answer
    score += int(right)
    verdict = "✅ Верно." if right else f"❌ Неверно. Правильный ответ: {LETTERS[q.answer]}) {escape(q.options[q.answer])}"
    text = (
        f"<b>Тест к уроку {lesson.id}</b> · вопрос {idx + 1} из {len(lesson.quiz)}\n\n"
        f"{escape(q.question)}\n\nТвой ответ: {LETTERS[choice]}) {escape(q.options[choice])}\n\n"
        f"{verdict}\n\n{escape(q.explain)}"
    )
    last = idx + 1 == len(lesson.quiz)
    nxt = f"qr:{lesson.id}:{score}" if last else f"qz:{lesson.id}:{idx + 1}:{score}"
    await call.message.edit_text(text, reply_markup=kb([btn("Результат" if last else "Дальше →", sign(secret, user_id, nxt))]))


async def show_result(call, lesson, score, ok, course, drafts, user_id, secret):
    total = len(lesson.quiz)
    rows = []
    if ok:
        text = f"<b>Тест сдан: {score} из {total}</b> ✅"
        if lesson.open_questions:
            text += "\n\nТеперь ответь своими словами на вопросы на понимание: так проверяется, что модель в голове верная."
            rows.append([btn("💬 Вопросы на понимание", f"oq:{lesson.id}")])
        following = [l for l in course.ordered(drafts) if l.id > lesson.id]
        if following:
            rows.append([btn(f"Урок {following[0].id} →", f"ls:{following[0].id}")])
    else:
        text = f"<b>{score} из {total}</b>. Для зачёта нужно больше. Перечитай главу и попробуй ещё раз."
        rows.append([btn("Пройти заново", sign(secret, user_id, f"qz:{lesson.id}:0:0"))])
    rows.append([btn("← К уроку", f"ls:{lesson.id}")])
    await call.message.edit_text(text, reply_markup=kb(*rows))
