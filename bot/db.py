from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, delete, func, inspect, select, text, update
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
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # старая схема тарифов, не используется
    credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # купленные разборы
    trial_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # истрачено пробных
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    """Покупка пакета разборов. Разборы начисляются один раз, при переходе в статус paid."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # stars | platega | yookassa | grant
    external_id: Mapped[str] = mapped_column(String(128), unique=True)  # id платежа у провайдера
    reviews: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))  # RUB | XTR
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | paid | canceled | refunded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        await conn.run_sync(_add_missing_columns)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _add_missing_columns(conn) -> None:
    """create_all не меняет существующие таблицы: новые столбцы добавляем сами."""
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(conn.dialect)}"
                if column.server_default is not None:
                    ddl += f" NOT NULL DEFAULT {column.server_default.arg}"
                conn.execute(text(ddl))


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


async def spend_review(session: AsyncSession, user_id: int, trial_limit: int) -> str | None:
    """Списать один разбор: сначала купленный, потом пробный. Возвращает "paid", "trial" или None, если списать нечего.

    Списание идёт одним UPDATE с условием, поэтому два одновременных ответа не уйдут в минус.
    """
    paid = await session.execute(
        update(User).where(User.id == user_id, User.credits > 0).values(credits=User.credits - 1))
    kind = "paid" if paid.rowcount else None
    if kind is None:
        trial = await session.execute(
            update(User).where(User.id == user_id, User.trial_used < trial_limit).values(trial_used=User.trial_used + 1))
        kind = "trial" if trial.rowcount else None
    await session.commit()
    return kind


async def refund_review(session: AsyncSession, user_id: int, kind: str) -> None:
    """Вернуть списанный разбор, если проверка не состоялась."""
    values = {"credits": User.credits + 1} if kind == "paid" else {"trial_used": User.trial_used - 1}
    await session.execute(update(User).where(User.id == user_id).values(**values))
    await session.commit()


async def confirm_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    """Перевести платёж в paid и начислить разборы. Повторный вызов ничего не делает и возвращает None."""
    done = await session.execute(
        update(Payment).where(Payment.id == payment_id, Payment.status != "paid")
        .values(status="paid", paid_at=utcnow()))
    if not done.rowcount:
        await session.rollback()
        return None
    payment = await session.get(Payment, payment_id)
    await session.execute(update(User).where(User.id == payment.user_id).values(credits=User.credits + payment.reviews))
    await session.commit()
    await session.refresh(payment)
    return payment

async def reset_progress(session: AsyncSession, user_id: int, lesson_id: int | None = None) -> int:
    """Стереть результаты тестов: одного урока или всех. Возвращает число стёртых записей."""
    query = delete(Progress).where(Progress.user_id == user_id)
    if lesson_id is not None:
        query = query.where(Progress.lesson_id == lesson_id)
    done = await session.execute(query)
    await session.commit()
    return done.rowcount


DELETED_USER_ID = 0  # на него переписывается учёт разборов удалённых пользователей


async def forget_user(session: AsyncSession, user_id: int) -> None:
    """Удалить данные пользователя по его запросу.

    Остаются: идентификатор со счётчиком пробных разборов (иначе пробный запас выдавался бы заново)
    и платежи (их хранения требует закон). Расходы на ИИ обезличиваются, чтобы не поплыл учёт бюджета.
    """
    if await session.get(User, DELETED_USER_ID) is None:
        session.add(User(id=DELETED_USER_ID))
    await session.execute(delete(Progress).where(Progress.user_id == user_id))
    await session.execute(update(AIUsage).where(AIUsage.user_id == user_id).values(user_id=DELETED_USER_ID))
    await session.execute(update(User).where(User.id == user_id).values(username=None, first_name=None, credits=0))
    await session.commit()


async def refund_payment(session: AsyncSession, payment_id: int) -> int | None:
    """Пометить платёж возвращённым и списать его разборы (сколько осталось, но не больше пакета).

    Возвращает число списанных разборов или None, если платёж не в статусе paid.
    """
    done = await session.execute(
        update(Payment).where(Payment.id == payment_id, Payment.status == "paid").values(status="refunded"))
    if not done.rowcount:
        await session.rollback()
        return None
    payment = await session.get(Payment, payment_id)
    user = await session.get(User, payment.user_id)
    taken = min(user.credits, payment.reviews)
    await session.execute(update(User).where(User.id == user.id).values(credits=User.credits - taken))
    await session.commit()
    return taken


async def budget_balance(session: AsyncSession) -> float:
    topped = await session.scalar(select(func.coalesce(func.sum(BudgetTopup.amount_usd), 0.0)))
    spent = await session.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)))
    return float(topped) - float(spent)
