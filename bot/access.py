"""Доступ к урокам, разборы ИИ и подпись callback-данных."""
import hashlib
import hmac
from enum import StrEnum

from bot.config import Settings
from bot.content import Course, Lesson
from bot.db import User


class Tier(StrEnum):
    FREE = "free"
    ADMIN = "admin"


def tier_of(user: User, settings: Settings) -> Tier:
    return Tier.ADMIN if user.id in settings.admin_ids else Tier.FREE


def trial_left(user: User, settings: Settings) -> int:
    return max(0, settings.trial_reviews - user.trial_used)


def model_for(kind: str, settings: Settings) -> str:
    """Модель для разбора: kind - что списали ("paid", "trial") или "admin"."""
    return {"paid": settings.model_paid, "trial": settings.model_free, "admin": settings.model_admin}[kind]


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
