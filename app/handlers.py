"""Private-chat entry points; no FSM or raw-update storage."""

import logging

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.sessions import SessionManager

logger = logging.getLogger(__name__)


def make_router(manager: SessionManager) -> Router:
    router = Router()

    @router.message()
    async def message_handler(message: Message) -> None:
        sender = message.from_user
        if (
            message.chat.type != ChatType.PRIVATE
            or sender is None
            or sender.is_bot
            or message.chat.id != sender.id
        ):
            return
        if not message.text:
            return  # QR images and documents are outside this version's input interface.
        command = message.text.strip().lower()
        if command in ("/start", "/help"):
            await manager.welcome(sender.id, message.chat.id)
        elif command == "/cancel":
            await manager.cancel(sender.id, message.chat.id, message.message_id)
        elif command == "/generate":
            await manager.prompt(sender.id, message.chat.id, message.message_id)
        else:
            # Includes commands with arguments: do not accidentally leave a secret in chat.
            await manager.submit(sender.id, message.chat.id, message.message_id, message.text)

    @router.callback_query()
    async def callback_handler(query: CallbackQuery) -> None:
        message = query.message
        accepted = False
        if (
            isinstance(message, Message)
            and message.chat.type == ChatType.PRIVATE
            and message.chat.id == query.from_user.id
            and not query.from_user.is_bot
        ):
            if query.data == "generate":
                accepted = await manager.prompt(query.from_user.id, message.chat.id)
            elif query.data and ":" in query.data:
                action, token = query.data.split(":", 1)
                accepted = await manager.control(
                    query.from_user.id,
                    message.chat.id,
                    message.message_id,
                    token,
                    action,
                )
        try:
            await query.answer(None if accepted else "This session is unavailable.")
        except TelegramAPIError:
            pass

    @router.errors()
    async def suppress_sensitive_errors(event: ErrorEvent) -> bool:
        # ErrorEvent contains the full update; neither it nor its exception is logged.
        logger.error("Update failed; request details suppressed.")
        return True

    return router
