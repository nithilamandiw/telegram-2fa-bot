"""Deployment configuration; never accept authentication secrets in settings."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.utils.token import TokenValidationError, validate_token

SESSION_SECONDS = 300
DISPLAY_INTERVAL_SECONDS = 5
PROMPT_SECONDS = 60
REQUEST_SECONDS = 5
MAX_TELEGRAM_ID = 2**52 - 1


def telegram_user_id(value: str) -> int:
    value = value.strip()
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= MAX_TELEGRAM_ID:
        raise ValueError("A positive numeric Telegram user ID is required.")
    return int(value)


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    timezone: ZoneInfo
    owner_telegram_id: int
    auth_db_path: Path = Path("data/access.sqlite3")

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("BOT_TOKEN", "").strip()
        try:
            validate_token(token)
        except TokenValidationError:
            raise ValueError("Set BOT_TOKEN to the token issued by BotFather.") from None
        try:
            timezone = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "Asia/Colombo"))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("DISPLAY_TIMEZONE must be a valid IANA timezone.") from None
        try:
            owner_id = telegram_user_id(os.environ.get("OWNER_TELEGRAM_ID", ""))
        except ValueError:
            raise ValueError("Set OWNER_TELEGRAM_ID to your numeric Telegram user ID.") from None
        database = os.environ.get("AUTH_DB_PATH", "data/access.sqlite3").strip()
        if not database or database == ":memory:":
            raise ValueError("AUTH_DB_PATH must name a persistent SQLite file.")
        return cls(token, timezone, owner_id, Path(database))
