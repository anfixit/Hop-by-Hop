"""Данные пользователя: своя статистика, сброс прогресса, удаление данных."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from bot.config import Settings
from bot.content import Course
from bot.db import AIUsage, Progress, forget_user, passed_lessons, reset_progress, upsert_user
from bot.handlers.common import btn, kb, lesson_title, sees_drafts
from bot.handlers.payments import balance_line, reviews_word

router = Router(name="account")


@router.message(Command("me"))
async def cmd_me(message: Message, db, settings: Settings, course: Course) -> None:
    async with db() as session:
        user = await upsert_user(session, message.from_user)
        passed = await passed_lessons(session, user.id)
        attempts = await session.scalar(select(func.count()).select_from(Progress).where(Progress.user_id == user.id))
        reviews = await session.scalar(select(func.count()).select_from(AIUsage).where(AIUsage.user_id == user.id))
    drafts = sees_drafts(user, settings)
    lessons = [l for l in sorted(course.lessons.values(), key=lambda l: l.id) if l.published or drafts]
    done = [l for l in lessons if l.id in passed]
    todo = next((l for l in lessons if l.id not in passed), None)
    lines = [
        "<b>Моя статистика</b>", "",
        f"В курсе с {user.created_at:%d.%m.%Y}",
        f"Сдано тестов: {len(done)} из {len(lessons)}" + (f" ({len(done) / len(lessons):.0%})" if lessons else ""),
    ]
    if attempts > len(done):
        lessons_word = "уроке" if attempts - len(done) == 1 else "уроках"
        lines.append(f"Тест начат, но не сдан: в {attempts - len(done)} {lessons_word}")
    lines.append(f"Дальше: {lesson_title(course, todo.id)}" if todo else "Все опубликованные уроки пройдены 🎉")
    lines += ["", f"Получено разборов ИИ: {reviews}", balance_line(user, settings),
              "", "/reset - сбросить прогресс, /forget - удалить мои данные"]
    await message.answer("\n".join(lines), reply_markup=kb([btn("Оглавление", "toc:0")]))


# --- Сброс прогресса ---

@router.message(Command("reset"))
async def cmd_reset(message: Message, command: CommandObject, db, course: Course) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            "<b>Сброс прогресса</b>\n\n"
            "Сбросить можно всё сразу или один урок: для одного напиши номер, например /reset 12.\n\n"
            "Сбрасываются результаты тестов. Разборы ИИ и покупки не затрагиваются.",
            reply_markup=kb([btn("Сбросить весь прогресс", "rst:all")]))
        return
    if not arg.isdigit() or int(arg) not in course.lessons:
        await message.answer("Такого урока нет. Пример: /reset 12")
        return
    await message.answer(**_confirm(course, arg))


@router.callback_query(F.data == "rst:all")
async def cb_reset_all(call: CallbackQuery, course: Course) -> None:
    await call.message.edit_text(**_confirm(course, "all"))
    await call.answer()


def _confirm(course: Course, what: str) -> dict:
    if what == "all":
        text = "Сбросить результаты всех тестов? Курс начнётся с первого урока. Отменить сброс нельзя."
    else:
        text = (f"Сбросить тест: {lesson_title(course, int(what))}? Следующий за ним урок закроется, "
                "пока тест не будет сдан заново. Отменить сброс нельзя.")
    return dict(text=text, reply_markup=kb([btn("Да, сбросить", f"rstok:{what}"), btn("Отмена", "acc:no")]))


@router.callback_query(F.data.startswith("rstok:"))
async def cb_reset_ok(call: CallbackQuery, db) -> None:
    what = call.data.split(":")[1]
    async with db() as session:
        await upsert_user(session, call.from_user)
        count = await reset_progress(session, call.from_user.id, None if what == "all" else int(what))
    done = "Нечего сбрасывать: тест ещё не проходился." if not count else (
        "Прогресс сброшен." if what == "all" else f"Тест урока {what} сброшен.")
    await call.message.edit_text(done + "\n\n/lessons - к урокам.")
    await call.answer()


# --- Удаление данных ---

@router.message(Command("forget"))
async def cmd_forget(message: Message, db) -> None:
    async with db() as session:
        user = await upsert_user(session, message.from_user)
    lines = [
        "<b>Удаление данных</b>", "",
        "Будут удалены: имя и username, результаты всех тестов, связь учёта разборов с тобой.",
        "Останутся: твой идентификатор Telegram со счётчиком пробных разборов (чтобы пробный запас "
        "не выдавался заново) и записи о платежах (их хранения требует закон).",
    ]
    if user.credits:
        lines += ["", f"⚠️ У тебя {reviews_word(user.credits)} из купленных пакетов. Они пропадут. "
                      "Если нужен возврат, сначала напиши в /paysupport."]
    lines += ["", "Отменить удаление нельзя."]
    await message.answer("\n".join(lines),
                         reply_markup=kb([btn("Да, удалить мои данные", "fgt:ok"), btn("Отмена", "acc:no")]))


@router.callback_query(F.data == "fgt:ok")
async def cb_forget_ok(call: CallbackQuery, db) -> None:
    async with db() as session:
        await forget_user(session, call.from_user.id)
    await call.message.edit_text("Данные удалены. Если вернёшься, нажми /start: курс начнётся с первого урока.")
    await call.answer()


@router.callback_query(F.data == "acc:no")
async def cb_cancel(call: CallbackQuery) -> None:
    await call.message.edit_text("Отменено, ничего не изменилось.")
    await call.answer()
