"""Private-chat entry points; no FSM or raw-update storage."""

import logging

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.access import AccessService, AccessUnavailable
from app.config import telegram_user_id
from app.sessions import SessionManager

logger = logging.getLogger(__name__)


def make_router(manager: SessionManager, access: AccessService) -> Router:
    router = Router()

    async def handle_message(message: Message) -> None:
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
        if command == "/whoami":
            await access.send(sender.id, f"Your Telegram user ID: <code>{sender.id}</code>")
            return
        parts = command.split()
        if sender.id == access.owner_id:
            if command in ("/requests", "/users"):
                await access.listing(sender.id, command[1:])
                return
            if parts and parts[0] in ("/approve", "/revoke"):
                await access.delete_submission(sender.id, message.message_id)
                try:
                    if len(parts) != 2:
                        raise ValueError
                    user_id = telegram_user_id(parts[1])
                except ValueError:
                    await access.send(sender.id, f"Usage: {parts[0]} &lt;numeric_user_id&gt;")
                    return
                changed = await access.change(
                    sender.id, user_id, "approved" if parts[0] == "/approve" else "revoked"
                )
                await access.send(
                    sender.id,
                    f"Access updated for {user_id}." if changed else "Owner access cannot change.",
                )
                return
        # Hold this lock through admission and the operation. Revocation acquires the
        # same lock, then removes access and cancels any session before confirming.
        async with access.lock(sender.id):
            try:
                allowed = access.allowed(sender.id)
            except AccessUnavailable:
                await access.delete_submission(sender.id, message.message_id)
                raise
            if not allowed:
                if command not in ("/start", "/help"):
                    await access.delete_submission(sender.id, message.message_id)
                await access.screen(sender.id)
                return
            if command in ("/start", "/help"):
                await manager.welcome(sender.id, message.chat.id)
            elif command == "/cancel":
                await manager.cancel(sender.id, message.chat.id, message.message_id)
            elif command == "/generate":
                await manager.prompt(sender.id, message.chat.id, message.message_id)
            else:
                # Includes commands with arguments: do not leave a secret in chat.
                await manager.submit(sender.id, message.chat.id, message.message_id, message.text)

    async def storage_failed(user_id: int) -> None:
        await manager.cancel(user_id, user_id)
        logger.error("Authorization unavailable; access denied. Request details suppressed.")
        await access.send(user_id, "Access could not be verified. Please try again later.")

    @router.message()
    async def message_handler(message: Message) -> None:
        try:
            await handle_message(message)
        except AccessUnavailable:
            await storage_failed(message.chat.id)

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
            user_id = query.from_user.id
            data = query.data or ""
            try:
                if data == "access:request":
                    accepted = await access.request(user_id, query.from_user.full_name)
                elif data.startswith("access:"):
                    accepted = await access.callback(user_id, data)
                else:
                    async with access.lock(user_id):
                        if not access.allowed(user_id):
                            await access.screen(user_id)
                        elif data == "generate":
                            accepted = await manager.prompt(user_id, message.chat.id)
                        elif ":" in data:
                            action, token = data.split(":", 1)
                            accepted = await manager.control(
                                user_id, message.chat.id, message.message_id, token, action
                            )
            except AccessUnavailable:
                await storage_failed(user_id)
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
