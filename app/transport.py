"""A small transport boundary; API failures never expose requests or response bodies."""

from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class Unavailable(Exception):
    """A request could not complete; safe to describe without Telegram details."""


class MessageGone(Unavailable):
    pass


class RateLimited(Unavailable):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__("Telegram request rate limited.")


Buttons = tuple[tuple[str, str], ...]


class Transport(Protocol):
    async def send(self, chat_id: int, text: str, buttons: Buttons = ()) -> int: ...
    async def edit(self, chat_id: int, message_id: int, text: str, buttons: Buttons) -> None: ...
    async def delete(self, chat_id: int, message_id: int) -> None: ...


def keyboard(buttons: Buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=data)
                for label, data in buttons[i : i + 2]
            ]
            for i in range(0, len(buttons), 2)
        ]
    )


class TelegramTransport:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send(self, chat_id: int, text: str, buttons: Buttons = ()) -> int:
        try:
            message = await self.bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=keyboard(buttons),
                protect_content=True,
            )
            return message.message_id
        except TelegramRetryAfter as exc:
            raise RateLimited(exc.retry_after) from None
        except TelegramAPIError:
            raise Unavailable() from None

    async def edit(self, chat_id: int, message_id: int, text: str, buttons: Buttons) -> None:
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML",
                reply_markup=keyboard(buttons),
            )
        except TelegramRetryAfter as exc:
            raise RateLimited(exc.retry_after) from None
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
            if "message to edit not found" in exc.message.lower():
                raise MessageGone() from None
            raise Unavailable() from None
        except TelegramAPIError:
            raise Unavailable() from None

    async def delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramRetryAfter as exc:
            raise RateLimited(exc.retry_after) from None
        except TelegramBadRequest as exc:
            if "message to delete not found" in exc.message.lower():
                return  # Idempotent cleanup.
            raise Unavailable() from None
        except TelegramAPIError:
            raise Unavailable() from None
