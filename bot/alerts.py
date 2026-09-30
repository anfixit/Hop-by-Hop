"""Оповещения админам: ошибки из журнала и ежедневная копия базы."""
import asyncio
import gzip
import logging
import sqlite3
import tempfile
import time
from datetime import timedelta
from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import Settings
from bot.db import utcnow
from bot.handlers.common import notify_admins

log = logging.getLogger(__name__)

REPEAT_AFTER = 600  # одну и ту же ошибку повторяем не чаще раза в 10 минут
MAX_PER_MINUTE = 5  # и не больше пяти разных в минуту, чтобы не завалить личку
BACKUP_HOUR_UTC = 1  # копия базы уходит админам раз в сутки, в 04:00 по Москве


class AdminAlertHandler(logging.Handler):
    """Пересылает записи уровня ERROR админам в Telegram. Пользователи этих сообщений не видят."""

    def __init__(self, bot: Bot, settings: Settings):
        super().__init__(level=logging.ERROR)
        self.bot, self.settings = bot, settings
        self.loop = asyncio.get_running_loop()
        self.seen: dict[str, float] = {}
        self.recent: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "bot.handlers.common":  # там журналируется сбой самой отправки админу
            return
        now = time.monotonic()
        key = f"{record.name}:{record.lineno}"
        self.recent = [t for t in self.recent if now - t < 60]
        if now - self.seen.get(key, -REPEAT_AFTER) < REPEAT_AFTER or len(self.recent) >= MAX_PER_MINUTE:
            return
        self.seen[key] = now
        self.recent.append(now)
        text = f"🔥 <b>Ошибка в боте</b>\n{escape(record.name)}\n<pre>{escape(self.format(record)[-3000:])}</pre>"
        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(notify_admins(self.bot, self.settings, text)))


def dump_database(path: Path) -> bytes:
    """Согласованный снимок SQLite (через backup API, а не копированием файла), сжатый gzip."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.sqlite3"
        src, dst = sqlite3.connect(path), sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        return gzip.compress(copy.read_bytes())


async def send_backup(bot: Bot, settings: Settings) -> bool:
    path = settings.sqlite_path
    if path is None or not path.exists():
        return False
    data = await asyncio.to_thread(dump_database, path)
    name = f"hopbyhop-{utcnow():%Y%m%d-%H%M}.sqlite3.gz"
    for admin_id in settings.admin_ids:
        try:
            await bot.send_document(admin_id, BufferedInputFile(data, filename=name),
                                    caption="Резервная копия базы бота", disable_notification=True)
        except Exception:
            log.exception("Не удалось отправить копию базы админу %s", admin_id)
    return True


async def backup_daily(bot: Bot, settings: Settings) -> None:
    """Фоновая задача: раз в сутки отправляет админам копию базы."""
    while True:
        now = utcnow()
        target = now.replace(hour=BACKUP_HOUR_UTC, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await send_backup(bot, settings)
        except Exception:
            log.exception("Ошибка резервного копирования")
