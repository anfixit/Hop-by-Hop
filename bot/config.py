from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing import Annotated, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]


class Pack(NamedTuple):
    """Пакет разборов: сколько штук и сколько стоит в рублях и в звёздах Telegram."""

    reviews: int
    rub: int
    stars: int


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    anthropic_api_key: SecretStr | None = None
    telegraph_token: SecretStr | None = None
    proxy_url: str | None = None

    database_url: str = f"sqlite+aiosqlite:///{(REPO_ROOT / 'data' / 'bot.sqlite3').as_posix()}"

    model_free: str = "claude-haiku-4-5"
    model_paid: str = "claude-sonnet-5"
    model_admin: str = "claude-sonnet-5"

    # Разборы ИИ: пробный запас выдаётся один раз, дальше - купленные пакеты
    trial_reviews: int = 15
    # Пакеты: "разборов:рублей:звёзд" через запятую
    packs: Annotated[tuple[Pack, ...], NoDecode] = (Pack(50, 99, 75), Pack(350, 490, 450))
    bot_username: str = "hopbyhop_bot"

    # Касса Platega (оплата картой и СБП); без ключей в боте остаются только звёзды
    platega_merchant_id: str | None = None
    platega_secret: SecretStr | None = None
    # Номер способа оплаты в Platega; пусто - плательщик выбирает сам на форме
    platega_payment_method: int | None = None

    # Почта для сообщений об ошибках в уроках; без пароля сообщения приходят только в Telegram
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    report_email: str | None = None

    budget_alert_usd: float = 5.0
    quiz_pass_ratio: float = 0.8

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(part) for part in value.replace(" ", "").split(",") if part)
        return value

    @field_validator("packs", mode="before")
    @classmethod
    def _parse_packs(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(Pack(*(int(x) for x in part.split(":"))) for part in value.replace(" ", "").split(",") if part)
        return value

    @field_validator("proxy_url", "anthropic_api_key", "telegraph_token", "platega_merchant_id", "platega_secret",
                     "platega_payment_method", "smtp_user", "smtp_password", "report_email", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return value or None

    @property
    def platega_enabled(self) -> bool:
        return bool(self.platega_merchant_id and self.platega_secret)

    @property
    def report_email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.report_email)

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            path = Path(self.database_url.removeprefix(prefix))
            return path if path.is_absolute() else REPO_ROOT / path
        return None
