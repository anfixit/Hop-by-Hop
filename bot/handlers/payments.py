"""Покупка пакетов разборов и благодарность автору: звёзды Telegram и рублёвая касса (YooKassa или Platega)."""
import asyncio
import html
import json
import logging
from datetime import UTC, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.access import trial_left
from bot.config import Pack, Settings
from bot.content import TELEGRAPH_INDEX
from bot.db import Payment, User, confirm_payment, upsert_user, utcnow
from bot.handlers.common import btn, kb, notify_admins
from bot.platega import CANCELED, CHARGEBACKED, CONFIRMED, Platega, PlategaError, Transaction
from bot.yookassa import YooKassa, YooKassaError

log = logging.getLogger(__name__)
router = Router(name="payments")

POLL_EVERY = 15  # секунд между опросами кассы
CHECK_TIMEOUT = 8  # секунд ждём кассу по кнопке: Telegram не принимает ответ на нажатие, если ждать дольше
PENDING_TTL = timedelta(hours=2)  # дольше неоплаченный счёт не ждём

# Документы опубликованы в Telegraph (tools/publish_telegraph.py legal), адреса лежат в content/telegraph.json
LEGAL_DOCS = (
    ("offer", "Публичная оферта"),
    ("privacy", "Политика конфиденциальности"),
    ("rules", "Правила использования"),
)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def reviews_word(n: int) -> str:
    return f"{n} {plural(n, 'разбор', 'разбора', 'разборов')}"


def balance_line(user, settings: Settings) -> str:
    trial = trial_left(user, settings)
    line = f"У тебя: {reviews_word(user.credits)}"
    return line + (f" и {trial} {plural(trial, 'пробный', 'пробных', 'пробных')}" if trial else "")


CashboxError = (PlategaError, YooKassaError)


def rub_cashbox(platega: Platega | None, yookassa: YooKassa | None) -> tuple[str, Platega | YooKassa | None]:
    """Через какую кассу выставлять новый рублёвый счёт: YooKassa заменяет Platega, если подключена."""
    return ("yookassa", yookassa) if yookassa is not None else ("platega", platega)


async def create_rub_invoice(platega: Platega | None, yookassa: YooKassa | None, *args) -> tuple[str, Transaction]:
    """Выставить рублёвый счёт: сначала YooKassa, если она не ответила - Platega. Ошибка, если не смогла ни одна."""
    error: Exception = PlategaError("нет подключённой кассы")
    for provider, cashbox in (("yookassa", yookassa), ("platega", platega)):
        if cashbox is None:
            continue
        try:
            return provider, await cashbox.create(*args)
        except CashboxError as exc:
            log.warning("Касса %s не выставила счёт (%s), пробуем следующую", provider, exc)
            error = exc
    raise error


def _pack(settings: Settings, idx: str) -> Pack | None:
    return settings.packs[int(idx)] if idx.isdigit() and int(idx) < len(settings.packs) else None


# Благодарность автору ("купить кофе"): платёж без разборов, reviews = 0. Суммы: рубли и звёзды.
DONATIONS = ((100, 75), (300, 225), (500, 375))


def _donation(idx: str) -> tuple[int, int] | None:
    return DONATIONS[int(idx)] if idx.isdigit() and int(idx) < len(DONATIONS) else None


async def shop(tg_user, db, settings: Settings):
    async with db() as session:
        user = await upsert_user(session, tg_user)
    lines = [
        "<b>Разборы ответов ИИ</b>", "",
        balance_line(user, settings) + ".", "",
        "Один разбор - один ответ на вопрос на понимание: ИИ находит ошибки и объясняет, как на самом деле. "
        "Каждый разбор - запрос к платной модели, поэтому они продаются пакетами. Пакет не сгорает.", "",
    ]
    rows = []
    for i, pack in enumerate(settings.packs):
        lines.append(f"• {reviews_word(pack.reviews)} - {pack.stars} ⭐" + (f" или {pack.rub} ₽" if settings.card_enabled else ""))
        row = [btn(f"{pack.reviews} за {pack.stars} ⭐", f"buy:s:{i}")]
        if settings.card_enabled:
            row.append(btn(f"{pack.reviews} за {pack.rub} ₽", f"buy:p:{i}"))
        rows.append(row)
    lines += ["", "Уроки, тесты и оглавление остаются бесплатными. Вопросы по оплате: /paysupport",
              "Оплачивая пакет, ты принимаешь оферту и правила: /terms"]
    rows.append([btn("☕ Поблагодарить автора", "donate")])
    rows.append([btn("← Оглавление", "toc:0")])
    return "\n".join(lines), kb(*rows)


def donate_screen(settings: Settings):
    text = (
        "<b>☕ Поблагодарить автора</b>\n\n"
        "Курс бесплатный и останется таким. Если он тебе помог и хочется сказать спасибо рублём или звездой - "
        "выбери сумму. Это добровольная благодарность: разборы за неё не начисляются, "
        "она просто помогает курсу жить и расти.\n\n"
        "Спасибо, что учишься вместе с Hop-by-Hop!\n\nОплачивая, ты принимаешь оферту (пункт 5.6): /terms"
    )
    rows = []
    for i, (rub, stars) in enumerate(DONATIONS):
        row = [btn(f"{stars} ⭐", f"don:s:{i}")]
        if settings.card_enabled:
            row.append(btn(f"{rub} ₽", f"don:p:{i}"))
        rows.append(row)
    rows.append([btn("← Оглавление", "toc:0")])
    return text, kb(*rows)


NUDGE_EVERY = 10  # напоминание о благодарности после каждого десятого пройденного урока


def donation_nudge(passed: int, donated: bool, settings: Settings):
    """Напоминание о благодарности после рубежа в NUDGE_EVERY уроков: текст и кнопки сумм."""
    lessons = f"{passed} {plural(passed, 'урок', 'урока', 'уроков')}"
    if donated:
        body = ("Спасибо за твою поддержку - она правда помогает курсу. Если захочется сказать спасибо ещё раз, "
                "кнопки ниже. А если нет - просто учись дальше, это главное.")
    else:
        body = ("Hop-by-Hop делает один человек: пишет уроки, проверяет каждый опыт на настоящем сервере, "
                "платит за сервер и за разборы ИИ. Курс бесплатный и останется таким, но живёт он на поддержке "
                "тех, кому он полезен.\n\nЕсли курс тебе помогает, поблагодари автора любой суммой - "
                "это занимает минуту и очень помогает выпускать новые уроки.")
    text = f"🎉 <b>Позади {lessons} курса!</b>\n\n{body}\n\nОплачивая, ты принимаешь оферту (пункт 5.6): /terms"
    return text, donate_screen(settings)[1]


async def has_donated(session, user_id: int) -> bool:
    return await session.scalar(select(Payment.id).where(
        Payment.user_id == user_id, Payment.reviews == 0, Payment.status == "paid").limit(1)) is not None


@router.message(Command("donate"))
async def cmd_donate(message: Message, settings: Settings) -> None:
    text, markup = donate_screen(settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "donate")
async def cb_donate(call: CallbackQuery, settings: Settings) -> None:
    text, markup = donate_screen(settings)
    await call.message.answer(text, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("don:s:"))
async def cb_donate_stars(call: CallbackQuery, bot: Bot) -> None:
    donation = _donation(call.data.split(":")[2])
    if donation is None:
        await call.answer("Такой суммы больше нет. Открой /donate заново.", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Благодарность автору курса",
        description="Добровольная благодарность автору курса Hop-by-Hop. Разборы за неё не начисляются.",
        payload=f"donate:{donation[1]}",
        currency="XTR",
        prices=[LabeledPrice(label="Благодарность", amount=donation[1])],
        provider_token="",
    )
    await call.answer()


@router.callback_query(F.data.startswith("don:p:"))
async def cb_donate_rub(call: CallbackQuery, db, settings: Settings, platega: Platega | None,
                       yookassa: YooKassa | None) -> None:
    donation = _donation(call.data.split(":")[2])
    if donation is None or rub_cashbox(platega, yookassa)[1] is None:
        await call.answer("Этот способ сейчас недоступен. Открой /donate заново.", show_alert=True)
        return
    await call.answer()
    rub = donation[0]
    try:
        provider, tx = await create_rub_invoice(platega, yookassa, rub, "Hop-by-Hop: благодарность автору",
                                                f"https://t.me/{settings.bot_username}", f"tg:{call.from_user.id}")
    except CashboxError:
        await call.message.answer("Не получилось создать счёт в кассе. Попробуй чуть позже или поблагодари звёздами.")
        return
    async with db() as session:
        await upsert_user(session, call.from_user)
        payment = Payment(user_id=call.from_user.id, provider=provider, external_id=tx.id,
                          reviews=0, amount=rub, currency="RUB")
        session.add(payment)
        await session.commit()
    await call.message.answer(
        f"Благодарность на {rub} ₽. Оплата откроется на странице кассы.",
        reply_markup=kb([btn("💳 Оплатить", url=tx.url)], [btn("Я оплатил, проверить", f"chk:{payment.id}")]),
    )


@router.message(Command("buy"))
async def cmd_buy(message: Message, db, settings: Settings) -> None:
    text, markup = await shop(message.from_user, db, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, db, settings: Settings) -> None:
    text, markup = await shop(call.from_user, db, settings)
    await call.message.answer(text, reply_markup=markup)
    await call.answer()


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(
        "Если оплата прошла, а разборы не начислились, или нужен возврат - напиши @Anfikus "
        "или на anfisa.kovganyuk@gmail.com. Укажи дату, сумму и способ оплаты, разберёмся."
    )


def terms_text() -> str:
    index = json.loads(TELEGRAPH_INDEX.read_text(encoding="utf-8")) if TELEGRAPH_INDEX.exists() else {}
    links = "\n".join(f'• <a href="{index[name]["url"]}">{title}</a>' for name, title in LEGAL_DOCS if name in index)
    return (
        "<b>Документы</b>\n\n"
        f"{links}\n\n"
        "Исполнитель: ИП Ковганюк Анфиса Валерьевна, ОГРНИП 324632700157665, ИНН 632418110551.\n"
        "Контакты: @Anfikus, anfisa.kovganyuk@gmail.com"
    )


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(terms_text(), disable_web_page_preview=True)


# --- Звёзды Telegram ---

@router.callback_query(F.data.startswith("buy:s:"))
async def cb_buy_stars(call: CallbackQuery, bot: Bot, settings: Settings) -> None:
    pack = _pack(settings, call.data.split(":")[2])
    if pack is None:
        await call.answer("Такого пакета больше нет. Открой /buy заново.", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"{reviews_word(pack.reviews)} ИИ",
        description="Разборы ответов на вопросы на понимание в курсе Hop-by-Hop. Не сгорают.",
        payload=f"pack:{pack.reviews}",
        currency="XTR",
        prices=[LabeledPrice(label=reviews_word(pack.reviews), amount=pack.stars)],
        provider_token="",
    )
    await call.answer()


def _stars_pack(settings: Settings, payload: str, total: int) -> Pack | None:
    """Пакет, которому соответствует оплаченный счёт: сверяем и число разборов, и сумму."""
    return next((p for p in settings.packs if payload == f"pack:{p.reviews}" and total == p.stars), None)


def _stars_donation(payload: str, total: int) -> bool:
    return any(payload == f"donate:{stars}" and total == stars for _, stars in DONATIONS)


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, settings: Settings) -> None:
    ok = query.currency == "XTR" and (
        _stars_pack(settings, query.invoice_payload, query.total_amount) is not None
        or _stars_donation(query.invoice_payload, query.total_amount))
    await query.answer(ok=ok, error_message=None if ok else "Цена пакета изменилась. Открой /buy заново.")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot, db, settings: Settings) -> None:
    sp = message.successful_payment
    pack = _stars_pack(settings, sp.invoice_payload, sp.total_amount)
    if pack is None and _stars_donation(sp.invoice_payload, sp.total_amount):
        pack = Pack(reviews=0, stars=sp.total_amount, rub=0)
    if pack is None:  # деньги получены, а пакет не опознан: не теряем платёж, зовём человека
        log.error("Оплата звёздами без пакета: payload=%s total=%s", sp.invoice_payload, sp.total_amount)
        await notify_admins(bot, settings, f"⚠️ Оплата звёздами не опознана: пользователь {message.from_user.id}, "
                                           f"{sp.total_amount} ⭐, payload {sp.invoice_payload}")
        await message.answer("Оплата получена, но пакет не опознан. Я уже сообщил автору курса, разборы начислят вручную.")
        return
    async with db() as session:
        await upsert_user(session, message.from_user)
        payment = Payment(user_id=message.from_user.id, provider="stars", external_id=sp.telegram_payment_charge_id,
                          reviews=pack.reviews, amount=sp.total_amount, currency="XTR")
        session.add(payment)
        try:
            await session.commit()
        except IntegrityError:  # Telegram прислал то же уведомление повторно
            return
        paid = await confirm_payment(session, payment.id)
    await _thank(bot, db, settings, paid)


# --- Рублёвая касса: YooKassa или Platega ---

@router.callback_query(F.data.startswith("buy:p:"))
async def cb_buy_rub(call: CallbackQuery, db, settings: Settings, platega: Platega | None,
                     yookassa: YooKassa | None) -> None:
    pack = _pack(settings, call.data.split(":")[2])
    if pack is None or rub_cashbox(platega, yookassa)[1] is None:
        await call.answer("Этот способ оплаты сейчас недоступен. Открой /buy заново.", show_alert=True)
        return
    await call.answer()
    try:
        provider, tx = await create_rub_invoice(platega, yookassa, pack.rub,
                                                f"Hop-by-Hop: {reviews_word(pack.reviews)} ИИ",
                                                f"https://t.me/{settings.bot_username}", f"tg:{call.from_user.id}")
    except CashboxError:
        await call.message.answer("Не получилось создать счёт в кассе. Попробуй чуть позже или оплати звёздами.")
        return
    async with db() as session:
        await upsert_user(session, call.from_user)
        payment = Payment(user_id=call.from_user.id, provider=provider, external_id=tx.id,
                          reviews=pack.reviews, amount=pack.rub, currency="RUB")
        session.add(payment)
        await session.commit()
    await call.message.answer(
        f"Счёт на {pack.rub} ₽ за {reviews_word(pack.reviews)}.\n\n"
        "Оплата откроется на странице кассы. Разборы начислятся сами в течение минуты после оплаты.",
        reply_markup=kb([btn("💳 Оплатить", url=tx.url)], [btn("Я оплатил, проверить", f"chk:{payment.id}")]),
    )


@router.callback_query(F.data.startswith("chk:"))
async def cb_check(call: CallbackQuery, bot: Bot, db, settings: Settings, platega: Platega | None,
                   yookassa: YooKassa | None) -> None:
    async with db() as session:
        payment = await session.get(Payment, int(call.data.split(":")[1]))
    cashbox = {"platega": platega, "yookassa": yookassa}.get(payment.provider) if payment else None
    if payment is None or payment.user_id != call.from_user.id or cashbox is None:
        await call.answer("Счёт не найден.", show_alert=True)
        return
    if payment.status == "paid":
        await call.answer("Этот счёт уже оплачен, спасибо!", show_alert=True)
        return
    try:
        state = await asyncio.wait_for(asyncio.shield(settle(bot, db, settings, cashbox, payment)), CHECK_TIMEOUT)
    except TimeoutError:  # опрос в фоне всё равно начислит разборы, когда касса ответит
        state = "error"
    await call.answer({
        "paid": "Оплата получена!",
        "canceled": "Счёт отменён или истёк. Создай новый через /buy.",
        "error": "Касса не отвечает, попробуй через минуту.",
    }.get(state, "Оплата пока не поступила. Если ты уже оплатил, подожди минуту."), show_alert=state != "paid")


async def settle(bot: Bot, db, settings: Settings, cashbox: Platega | YooKassa, payment: Payment) -> str:
    """Спросить кассу о платеже и, если он оплачен, начислить разборы. Возвращает paid, pending, canceled или error."""
    try:
        status, amount = await cashbox.status(payment.external_id)
    except CashboxError:
        return "error"
    if status == CONFIRMED:
        if amount is not None and amount < payment.amount:
            log.error("%s: платёж %s оплачен на %s вместо %s", payment.provider, payment.external_id, amount,
                      payment.amount)
            await notify_admins(bot, settings, f"⚠️ {payment.provider}: платёж {payment.external_id} оплачен на {amount} ₽ "
                                               f"вместо {payment.amount} ₽, разборы не начислены.")
            return "error"
        async with db() as session:
            paid = await confirm_payment(session, payment.id)
        if paid is not None:
            await _thank(bot, db, settings, paid)
        return "paid"
    if status in (CANCELED, CHARGEBACKED) or _expired(payment):
        async with db() as session:
            fresh = await session.get(Payment, payment.id)
            if fresh.status == "pending":
                fresh.status = "canceled"
                await session.commit()
        return "canceled"
    return "pending"


def _expired(payment: Payment) -> bool:
    created = payment.created_at
    if created.tzinfo is None:  # SQLite не хранит часовой пояс
        created = created.replace(tzinfo=UTC)
    return utcnow() - created > PENDING_TTL


async def poll_cashboxes(bot: Bot, db, settings: Settings, cashboxes: dict[str, Platega | YooKassa]) -> None:
    """Фоновая задача: раз в POLL_EVERY секунд проверяет неоплаченные счета подключённых касс."""
    while True:
        await asyncio.sleep(POLL_EVERY)
        try:
            async with db() as session:
                pending = (await session.scalars(
                    select(Payment).where(Payment.provider.in_(cashboxes), Payment.status == "pending"))).all()
            for payment in pending:
                await settle(bot, db, settings, cashboxes[payment.provider], payment)
        except Exception:  # опрос не должен умирать из-за одной ошибки
            log.exception("Ошибка опроса кассы")


def buyer(user: User | None, user_id: int) -> str:
    """Кто заплатил, для уведомления админу: имя-ссылка на профиль, @username и id."""
    name = html.escape(user.first_name or "без имени") if user else "без имени"
    parts = [f'<a href="tg://user?id={user_id}">{name}</a>']
    if user and user.username:
        parts.append(f"@{html.escape(user.username)}")
    return " ".join(parts) + f" (id {user_id})"


async def _thank(bot: Bot, db, settings: Settings, payment: Payment | None) -> None:
    if payment is None:
        return
    async with db() as session:
        user = await session.get(User, payment.user_id)
    sign_ = "⭐" if payment.currency == "XTR" else "₽"
    if payment.reviews == 0:  # благодарность автору
        try:
            await bot.send_message(payment.user_id, "☕ Спасибо огромное за поддержку! Это очень помогает курсу.\n\n"
                                                    "/lessons - к урокам.")
        except Exception:
            log.exception("Не удалось поблагодарить пользователя %s", payment.user_id)
        await notify_admins(bot, settings, f"☕ Благодарность: {payment.amount} {sign_} ({payment.provider}), "
                                           f"{buyer(user, payment.user_id)}")
        return
    try:
        await bot.send_message(payment.user_id, f"✅ Оплата получена: +{reviews_word(payment.reviews)}. "
                                                f"{balance_line(user, settings)}.\n\n/lessons - к урокам.")
    except Exception:
        log.exception("Не удалось сообщить пользователю %s об оплате", payment.user_id)
    await notify_admins(bot, settings, f"💰 Покупка: {reviews_word(payment.reviews)} за {payment.amount} {sign_} "
                                       f"({payment.provider}), {buyer(user, payment.user_id)}")
