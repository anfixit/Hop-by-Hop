from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.access import is_unlocked, sign
from bot.config import Settings
from bot.content import Course
from bot.db import passed_lessons, upsert_user
from bot.handlers.common import btn, kb, sees_drafts

router = Router(name="lessons")

PAGE_SIZE = 10
BUTTONS_PER_ROW = 5

WELCOME = (
    "<b>Hop-by-Hop</b> - курс по компьютерным сетям.\n\n"
    "Проходим путь пакета от кабеля до сервера и обратно: Ethernet, IP, TCP, DNS, NAT, "
    "Linux и netfilter, криптография, TLS, современные прокси-протоколы. "
    "В каждой главе разбираем атаки и защиту.\n\n"
    "Как это работает:\n"
    "1. Читаешь главу (она открывается прямо в Telegram).\n"
    "2. Проходишь тест. Следующая глава открывается, когда тест сдан.\n"
    "3. Отвечаешь своими словами на вопросы на понимание, ответы разбирает ИИ.\n\n"
    "Команды: /lessons - оглавление, /help - справка."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, db: async_sessionmaker[AsyncSession]) -> None:
    async with db() as session:
        await upsert_user(session, message.from_user)
    await message.answer(WELCOME, reply_markup=kb([btn("Оглавление", "toc:0")]))


@router.message(Command("lessons"))
async def cmd_lessons(message: Message, db, settings: Settings, course: Course) -> None:
    text, markup = await render_toc(message.from_user, 0, db, settings, course)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("toc:"))
async def cb_toc(call: CallbackQuery, db, settings: Settings, course: Course) -> None:
    page = int(call.data.split(":")[1])
    text, markup = await render_toc(call.from_user, page, db, settings, course)
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


async def render_toc(tg_user, page: int, db, settings: Settings, course: Course):
    async with db() as session:
        user = await upsert_user(session, tg_user)
        passed = await passed_lessons(session, user.id)
    drafts = sees_drafts(user, settings)
    lessons = course.ordered(drafts)
    if not lessons:
        return "Уроки ещё готовятся. Загляни чуть позже.", None

    pages = max(1, -(-len(lessons) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    # Длинные названия Telegram обрезает на кнопках, поэтому полные названия идут текстом,
    # а на кнопках только значок и номер урока, по BUTTONS_PER_ROW в ряд.
    lines, buttons = [], []
    for lesson in lessons[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        if lesson.id in passed:
            mark = "✅"
        elif is_unlocked(lesson, course, passed, drafts):
            mark = "▶️"
        else:
            mark = "🔒"
        draft = " [черновик]" if not lesson.published else ""
        lines.append(f"{mark} {lesson.id}. {escape(lesson.title)}{draft}")
        buttons.append(btn(f"{mark} {lesson.id}", f"ls:{lesson.id}"))
    rows = [buttons[i:i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    nav = []
    if page > 0:
        nav.append(btn("←", f"toc:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("→", f"toc:{page + 1}"))
    done = len(passed & {l.id for l in lessons})
    text = (
        f"<b>Оглавление</b>\nПройдено: {done} из {len(lessons)}. Страница {page + 1} из {pages}.\n\n"
        + "\n".join(lines)
        + "\n\nВыбери номер урока на кнопках ниже."
    )
    return text, kb(*rows, nav)


@router.callback_query(F.data.startswith("ls:"))
async def cb_lesson(call: CallbackQuery, db, settings: Settings, course: Course, secret: bytes) -> None:
    lesson_id = int(call.data.split(":")[1])
    async with db() as session:
        user = await upsert_user(session, call.from_user)
        passed = await passed_lessons(session, user.id)
    drafts = sees_drafts(user, settings)
    lesson = course.get(lesson_id, drafts)
    if lesson is None:
        await call.answer("Такого урока нет.", show_alert=True)
        return
    if not is_unlocked(lesson, course, passed, drafts):
        await call.answer("Сначала сдай тест предыдущего урока.", show_alert=True)
        return

    status = "Тест сдан ✅" if lesson.id in passed else "Тест ещё не сдан"
    text = f"<b>Урок {lesson.id}. {escape(lesson.title)}</b>\n\n{escape(lesson.summary)}\n\n{status}"
    read = btn("📖 Читать главу", url=lesson.telegraph_url) if lesson.telegraph_url else None
    if read is None and drafts:
        read = btn("📄 Текст черновика", f"md:{lesson.id}")
    rows = [
        [read] if read else [],
        [btn("📝 Пройти тест", sign(secret, user.id, f"qz:{lesson.id}:0:0"))],
    ]
    if lesson.id in passed and lesson.open_questions:
        rows.append([btn("💬 Вопросы на понимание", f"oq:{lesson.id}")])
    rows.append([btn("← Оглавление", "toc:0")])
    await call.message.edit_text(text, reply_markup=kb(*rows))
    await call.answer()


@router.callback_query(F.data.startswith("md:"))
async def cb_draft_text(call: CallbackQuery, db, settings: Settings, course: Course) -> None:
    lesson = course.get(int(call.data.split(":")[1]), include_drafts=call.from_user.id in settings.admin_ids)
    if lesson is None:
        await call.answer("Недоступно.", show_alert=True)
        return
    data = lesson.text_path.read_bytes()
    await call.message.answer_document(BufferedInputFile(data, filename=f"lesson-{lesson.id:03d}.md"))
    await call.answer()
