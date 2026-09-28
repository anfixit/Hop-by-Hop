"""Тарифы, доступ к урокам и подпись callback-данных."""
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from bot.config import Settings
from bot.content import Course, Lesson
from bot.db import User, utcnow


class Tier(StrEnum):
    FREE = "free"
    PAID = "paid"
    ADMIN = "admin"


@dataclass(frozen=True)
class TierPolicy:
    model: str
    daily_limit: int | None  # None - без лимита


def tier_of(user: User, settings: Settings) -> Tier:
    if user.id in settings.admin_ids:
        return Tier.ADMIN
    paid_until = user.paid_until
    if paid_until is not None:
        if paid_until.tzinfo is None:  # SQLite не хранит часовой пояс
            paid_until = paid_until.replace(tzinfo=UTC)
        if paid_until > utcnow():
            return Tier.PAID
    return Tier.FREE


def policy_for(tier: Tier, settings: Settings) -> TierPolicy:
    return {
        Tier.FREE: TierPolicy(settings.model_free, settings.limit_free_per_day),
        Tier.PAID: TierPolicy(settings.model_paid, settings.limit_paid_per_day),
        Tier.ADMIN: TierPolicy(settings.model_admin, None),
    }[tier]


def is_unlocked(lesson: Lesson, course: Course, passed: set[int], include_drafts: bool) -> bool:
    """Урок открыт, если это первый урок или пройден тест предыдущего."""
    previous = course.previous(lesson.id, include_drafts)
    return previous is None or previous.id in passed


# --- Подпись callback-данных ---
# Кнопки несут состояние теста (номер вопроса, счёт). Модифицированный клиент может
# прислать любые callback_data, поэтому состояние подписывается HMAC с привязкой к
# пользователю: подделать счёт или переиспользовать чужую кнопку не выйдет.

SIG_LEN = 10  # hex-символов; callback_data ограничены 64 байтами


def _sig(secret: bytes, user_id: int, payload: str) -> str:
    return hmac.new(secret, f"{user_id}|{payload}".encode(), hashlib.sha256).hexdigest()[:SIG_LEN]


def sign(secret: bytes, user_id: int, payload: str) -> str:
    data = f"{payload}|{_sig(secret, user_id, payload)}"
    if len(data.encode()) > 64:
        raise ValueError(f"callback_data длиннее 64 байт: {data}")
    return data


def verify(secret: bytes, user_id: int, data: str) -> str | None:
    payload, _, sig = data.rpartition("|")
    if payload and hmac.compare_digest(sig, _sig(secret, user_id, payload)):
        return payload
    return None
