from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing import Annotated

REPO_ROOT = Path(__file__).resolve().parents[1]


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

    limit_free_per_day: int = 3
    limit_paid_per_day: int = 30
    budget_alert_usd: float = 5.0
    quiz_pass_ratio: float = 0.8

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(part) for part in value.replace(" ", "").split(",") if part)
        return value

    @field_validator("proxy_url", "anthropic_api_key", "telegraph_token", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return value or None

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            path = Path(self.database_url.removeprefix(prefix))
            return path if path.is_absolute() else REPO_ROOT / path
        return None
