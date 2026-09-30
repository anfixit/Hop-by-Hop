from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bot.access import Tier, tier_of
from bot.config import Settings
from bot.db import (AIUsage, BudgetTopup, Payment, User, budget_balance, confirm_payment, init_db, passed_lessons,
                    record_quiz, refund_review, spend_review, upsert_user)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("ADMIN_IDS", "1, 2")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 't.sqlite3').as_posix()}")
    return Settings(_env_file=None)


def test_settings_parse(settings):
    assert settings.admin_ids == frozenset({1, 2})


async def test_progress_budget_and_tiers(settings):
    engine, db = await init_db(settings)
    try:
        async with db() as session:
            admin = await upsert_user(session, SimpleNamespace(id=1, username="a", first_name="A"))
            user = await upsert_user(session, SimpleNamespace(id=5, username="u", first_name="U"))
            assert tier_of(admin, settings) is Tier.ADMIN
            assert tier_of(user, settings) is Tier.FREE

            await record_quiz(session, 5, 1, 2, passed=False)
            assert await passed_lessons(session, 5) == set()
            await record_quiz(session, 5, 1, 3, passed=True)
            assert await passed_lessons(session, 5) == {1}

            assert await budget_balance(session) == 0
            session.add(BudgetTopup(amount_usd=10))
            session.add(AIUsage(user_id=5, lesson_id=1, model="m", input_tokens=1, output_tokens=1, cost_usd=0.25))
            await session.commit()
            assert await budget_balance(session) == pytest.approx(9.75)
    finally:
        await engine.dispose()


async def test_toc_buttons_are_short(settings):
    from bot.content import load_course
    from bot.handlers.lessons import render_toc

    engine, db = await init_db(settings)
    try:
        course = load_course()
        text, markup = await render_toc(SimpleNamespace(id=5, username="u", first_name="U"), 0, db, settings, course)
        buttons = [b for row in markup.inline_keyboard for b in row]
        # полные названия - в тексте, на кнопках только значок и номер
        assert all(len(b.text) <= 8 for b in buttons)
        first = course.ordered(False)[0]
        assert first.title.split(":")[0] in text
    finally:
        await engine.dispose()


async def test_reviews_are_spent_paid_first_then_trial(settings):
    engine, db = await init_db(settings)
    try:
        async with db() as session:
            await upsert_user(session, SimpleNamespace(id=5, username="u", first_name="U"))
            payment = Payment(user_id=5, provider="stars", external_id="ch1", reviews=2, amount=75, currency="XTR")
            session.add(payment)
            await session.commit()
            assert (await confirm_payment(session, payment.id)).status == "paid"
            assert await confirm_payment(session, payment.id) is None  # второй раз не начисляем

            kinds = [await spend_review(session, 5, trial_limit=2) for _ in range(5)]
            assert kinds == ["paid", "paid", "trial", "trial", None]

            await refund_review(session, 5, "trial")
            await refund_review(session, 5, "paid")
            user = await session.get(User, 5)
            await session.refresh(user)
            assert (user.credits, user.trial_used) == (1, 1)
    finally:
        await engine.dispose()


async def test_new_columns_are_added_to_an_old_database(settings):
    import sqlite3

    # база прежней версии: таблица users без столбцов credits и trial_used
    old = sqlite3.connect(settings.sqlite_path)
    old.execute("CREATE TABLE users (id BIGINT PRIMARY KEY, username VARCHAR(64), first_name VARCHAR(128), "
                "paid_until DATETIME, created_at DATETIME)")
    old.execute("INSERT INTO users (id, username) VALUES (7, 'old')")
    old.commit()
    old.close()

    engine, db = await init_db(settings)
    try:
        async with db() as session:
            user = await session.get(User, 7)
            assert (user.credits, user.trial_used) == (0, 0)
            assert await spend_review(session, 7, trial_limit=1) == "trial"
    finally:
        await engine.dispose()

async def test_platega_payment_is_credited_once_and_underpayment_is_not(settings):
    from bot.handlers.payments import settle

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))

    class FakePlatega:
        def __init__(self, answer):
            self.answer = answer

        async def status(self, transaction_id):
            return self.answer

    engine, db = await init_db(settings)
    try:
        async with db() as session:
            await upsert_user(session, SimpleNamespace(id=5, username="u", first_name="U"))
            for ext in ("ok", "short", "gone"):
                session.add(Payment(user_id=5, provider="platega", external_id=ext, reviews=50, amount=99, currency="RUB"))
            await session.commit()
            ok, short, gone = (await session.scalars(select(Payment).order_by(Payment.id))).all()

        bot = FakeBot()
        assert await settle(bot, db, settings, FakePlatega(("PENDING", 99)), ok) == "pending"
        assert await settle(bot, db, settings, FakePlatega(("CONFIRMED", 99)), ok) == "paid"
        assert await settle(bot, db, settings, FakePlatega(("CONFIRMED", 99)), ok) == "paid"  # повторный опрос
        assert await settle(bot, db, settings, FakePlatega(("CONFIRMED", 10)), short) == "error"
        assert await settle(bot, db, settings, FakePlatega(("CANCELED", 99)), gone) == "canceled"

        async with db() as session:
            user = await session.get(User, 5)
            assert user.credits == 50  # начислено один раз и только за полностью оплаченный счёт
            assert (await session.get(Payment, gone.id)).status == "canceled"
        # ученику одно сообщение об оплате; админам (id 1 и 2) - о покупке и о недоплате
        assert [chat for chat, _ in sent].count(5) == 1
        assert any("вместо" in text for _, text in sent)
    finally:
        await engine.dispose()