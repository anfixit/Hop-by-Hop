from types import SimpleNamespace

import pytest

from bot.access import Tier, tier_of
from bot.config import Settings
from bot.db import (AIUsage, BudgetTopup, budget_balance, init_db, passed_lessons, record_quiz, reviews_today,
                    upsert_user)


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
            assert await reviews_today(session, 5) == 1
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
