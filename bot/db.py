from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Progress(Base):
    __tablename__ = "progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    lesson_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    best_score: Mapped[int] = mapped_column(Integer, default=0)
    quiz_passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lesson_id: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BudgetTopup(Base):
    """Пополнения баланса Claude API, которые админ отмечает командой /topup."""

    __tablename__ = "budget_topups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    amount_usd: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


async def init_db(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    url = settings.database_url
    if (path := settings.sqlite_path) is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{path.as_posix()}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def upsert_user(session: AsyncSession, tg_user) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(id=tg_user.id)
        session.add(user)
    user.username = tg_user.username
    user.first_name = tg_user.first_name
    await session.commit()
    return user


async def passed_lessons(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.scalars(
        select(Progress.lesson_id).where(Progress.user_id == user_id, Progress.quiz_passed_at.is_not(None))
    )
    return set(rows)


async def record_quiz(session: AsyncSession, user_id: int, lesson_id: int, score: int, passed: bool) -> None:
    progress = await session.get(Progress, (user_id, lesson_id))
    if progress is None:
        progress = Progress(user_id=user_id, lesson_id=lesson_id)
        session.add(progress)
    progress.best_score = max(progress.best_score or 0, score)
    if passed and progress.quiz_passed_at is None:
        progress.quiz_passed_at = utcnow()
    await session.commit()


async def reviews_today(session: AsyncSession, user_id: int) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return await session.scalar(
        select(func.count()).select_from(AIUsage).where(AIUsage.user_id == user_id, AIUsage.created_at >= start)
    ) or 0


async def budget_balance(session: AsyncSession) -> float:
    topped = await session.scalar(select(func.coalesce(func.sum(BudgetTopup.amount_usd), 0.0)))
    spent = await session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)))
    return float(topped) - float(spent)
