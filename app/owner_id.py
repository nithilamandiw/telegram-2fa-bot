"""Print IDs from pending private /whoami messages before owner setup; never save updates."""

import asyncio
import os

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.utils.token import TokenValidationError, validate_token

from app.main import configure_logging


async def lookup(token: str) -> list[int]:
    async with Bot(token) as bot:
        updates = await bot.get_updates(limit=100, timeout=0, request_timeout=15)
    return sorted(
        {
            message.from_user.id
            for update in updates
            if (message := update.message) is not None
            and message.chat.type == ChatType.PRIVATE
            and message.from_user is not None
            and not message.from_user.is_bot
            and message.chat.id == message.from_user.id
            and message.text is not None
            and message.text.strip().lower() == "/whoami"
        }
    )


def main() -> None:
    configure_logging()
    token = os.environ.get("BOT_TOKEN", "").strip()
    try:
        validate_token(token)
    except TokenValidationError:
        print("Set BOT_TOKEN before looking up your Telegram user ID.")
        raise SystemExit(1) from None
    try:
        user_ids = asyncio.run(lookup(token))
    except Exception:
        print("ID lookup failed. Stop other bot instances and check the token and network.")
        raise SystemExit(1) from None
    if not user_ids:
        print(
            "No ID found in the first 100 pending updates. With the bot stopped, "
            "send /whoami in its private chat, then retry."
        )
        raise SystemExit(1)
    print("Telegram user IDs from pending private /whoami messages:")
    for user_id in user_ids:
        print(user_id)


if __name__ == "__main__":
    main()
