"""Deployment configuration; never accept authentication secrets in settings."""

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.utils.token import TokenValidationError, validate_token

SESSION_SECONDS = 300
DISPLAY_INTERVAL_SECONDS = 5
PROMPT_SECONDS = 60
REQUEST_SECONDS = 5


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    timezone: ZoneInfo

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
        return cls(bot_token=token, timezone=timezone)
