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
from bot.review import MAX_ANSWER_CHARS

router = Router(name="lessons")

PAGE_SIZE = 10
BUTTONS_PER_ROW = 5

def welcome_text(settings: Settings, course: Course) -> str:
    lessons = course.ordered(False)
    card = " или картой" if settings.platega_enabled else ""
    return (
        "<b>Hop-by-Hop</b> - курс по компьютерным сетям, от кабеля до современных прокси-протоколов, "
        "с упором на безопасность.\n\n"
        "<b>На чём основан</b>\n"
        "Учебники Олифера и Таненбаума, книга Ристича о TLS, стандарты RFC и документация Linux. "
        "Каждый опыт из уроков реально запущен на Linux, в тексте настоящий вывод команд. "
        "Каждый урок дважды проверен на ошибки перед публикацией.\n\n"
        "<b>Из чего состоит</b>\n"
        "14 блоков, 105 уроков: Ethernet, IP и маршрутизация, TCP и UDP, сетевой стек Linux, файерволы и NAT, DNS, криптография, TLS, "
        f"прокси и туннели. Сейчас готово уроков: {len(lessons)}, новые выходят постоянно.\n\n"
        "<b>Как проходить</b>\n"
        "1. Читаешь урок (открывается прямо в Telegram).\n"
        "2. Сдаёшь тест, и открывается следующий урок.\n"
        "3. Отвечаешь своими словами на вопросы на понимание.\n\n"
        "<b>Что бесплатно</b>\n"
        "Все уроки и все тесты.\n\n"
        "<b>Что платно и почему</b>\n"
        "Разбор твоего ответа ИИ: он находит ошибки в понимании и объясняет, как на самом деле. "
        "Каждый такой разбор - запрос к платной модели, за который платит автор курса. "
        f"Поэтому первые разборы ({settings.trial_reviews}) в подарок, дальше пакетами за звёзды Telegram{card}. "
        "Пакет не сгорает.\n\n"
        "/lessons - оглавление, /buy - разборы, /help - справка"
    )


def help_text(settings: Settings) -> str:
    card = " или картой через кассу" if settings.platega_enabled else ""
    return (
        "<b>Справка</b>\n\n"
        "<b>Команды</b>\n"
        "/lessons - оглавление и твой прогресс\n"
        "/buy - сколько разборов осталось и покупка пакета\n"
        "/help - эта справка\n"
        "/cancel - отменить ответ на вопрос\n"
        "/terms - оферта, политика конфиденциальности, правила\n"
        "/start - о курсе\n\n"
        "<b>Значки в оглавлении</b>\n"
        "✅ тест сдан, ▶️ урок открыт, 🔒 откроется после теста предыдущего урока.\n\n"
        "<b>Тест</b>\n"
        f"Один верный вариант в каждом вопросе. Тест сдан, если верных ответов не меньше "
        f"{settings.quiz_pass_ratio:.0%}. Пересдавать можно сколько угодно, после каждого "
        "ответа бот объясняет, почему так.\n\n"
        "<b>Вопросы на понимание</b>\n"
        "Открываются после сданного теста. Нажми \"Ответить\", напиши ответ своими словами "
        f"одним сообщением (до {MAX_ANSWER_CHARS} символов). ИИ разберёт его: что верно, "
        "чего не хватает и где ошибка в понимании. Отвечать можно повторно, каждый ответ - один разбор.\n\n"
        "<b>Разборы и оплата</b>\n"
        f"Уроки и тесты бесплатны. Разборы ИИ платные: первые {settings.trial_reviews} в подарок, "
        f"дальше пакетами в /buy, за звёзды Telegram{card}. Пакет не сгорает. "
        "Если проверка не удалась по нашей вине, разбор не списывается. Вопросы по оплате: /paysupport\n\n"
        "<b>Источники</b>\n"
        "• Олифер В., Олифер Н. Компьютерные сети. Принципы, технологии, протоколы. "
        "Юбилейное (6-е) издание. СПб.: Питер, 2021.\n"
        "• Таненбаум Э., Фимстер Н., Уэзеролл Д. Компьютерные сети. 6-е изд. СПб.: Питер, 2023.\n"
        "• Ristić I. Bulletproof TLS and PKI. 2nd ed. Feisty Duck, 2022.\n"
        "• Стандарты RFC, документация ядра Linux, страницы man, документация и исходный код программ.\n"
        "Уроки написаны своими словами, текст книг в них не воспроизводится. В конце каждого урока, "
        "в разделе \"Что почитать\", указаны главы и страницы этих изданий. Где книга устарела или ошибается, "
        "в уроке это отмечено и дана ссылка на первоисточник. Команды и опыты запускались на Ubuntu 24.04, "
        "в уроках настоящий вывод.\n\n"
        "<b>Если что-то не работает</b>\n"
        "Кнопка \"устарела\" - открой урок заново через /lessons. "
        "Бот ждёт ответ, а ты передумал - /cancel. "
        "Урок открывается в Telegraph: если страница не грузится, проверь доступ к telegra.ph.\n\n"
        "Нашёл ошибку в уроке? Напиши: github.com/anfixit/Hop-by-Hop/issues, "
        "в Telegram @Anfikus или на почту anfisa.kovganyuk@gmail.com"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: async_sessionmaker[AsyncSession], settings: Settings, course: Course) -> None:
    async with db() as session:
        await upsert_user(session, message.from_user)
    await message.answer(welcome_text(settings, course), reply_markup=kb([btn("Оглавление", "toc:0")]))


@router.message(Command("help"))
async def cmd_help(message: Message, db: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    async with db() as session:
        await upsert_user(session, message.from_user)
    await message.answer(help_text(settings), reply_markup=kb([btn("Оглавление", "toc:0")]),
                         disable_web_page_preview=True)

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
