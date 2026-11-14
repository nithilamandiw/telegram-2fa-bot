"""Run with python -m app.main or telegram-totp."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
from aiogram.utils.token import extract_bot_id

from app.access import AccessService, AccessStore
from app.config import REQUEST_SECONDS, Settings
from app.handlers import make_router
from app.sessions import SessionManager
from app.transport import TelegramTransport

logger = logging.getLogger("app.main")


class ApplicationLogsOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "app" or record.name.startswith("app.")


def configure_logging() -> None:
    # Third-party request/exception logs may contain bot tokens or message payloads.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(ApplicationLogsOnly())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("aiogram").setLevel(logging.CRITICAL)


async def run(settings: Settings) -> None:
    bot = Bot(settings.bot_token, session=AiohttpSession(timeout=REQUEST_SECONDS))
    manager = SessionManager(TelegramTransport(bot), settings.timezone)
    store = None
    try:
        store = AccessStore(settings.auth_db_path, extract_bot_id(settings.bot_token))
        access = AccessService(store, settings.owner_telegram_id, manager)
        dispatcher = Dispatcher(disable_fsm=True)
        dispatcher.include_router(make_router(manager, access))
        info = await bot.get_me()
        if info.can_join_groups or info.supports_inline_queries:
            raise ValueError("Disable group joining and inline mode through BotFather.")
        # Discard pending updates from downtime instead of processing old secret messages.
        # Such messages cannot be reliably cleaned after a crash without saved identifiers.
        await bot.delete_webhook(drop_pending_updates=True)
        commands = [
            BotCommand(command="start", description="Request access or start a private session"),
            BotCommand(command="generate", description="Generate temporary 2FA codes"),
            BotCommand(command="cancel", description="Stop and delete this session"),
            BotCommand(command="help", description="Privacy and usage information"),
            BotCommand(command="whoami", description="Show your Telegram user ID"),
        ]
        await bot.set_my_commands(
            commands,
            scope=BotCommandScopeAllPrivateChats(),
        )
        try:
            await bot.set_my_commands(
                commands
                + [
                    BotCommand(command="requests", description="Review pending access requests"),
                    BotCommand(command="users", description="List approved users"),
                    BotCommand(command="approve", description="Approve a numeric Telegram user ID"),
                    BotCommand(command="revoke", description="Revoke a numeric Telegram user ID"),
                ],
                scope=BotCommandScopeChat(chat_id=settings.owner_telegram_id),
            )
        except TelegramAPIError:
            # Telegram may not know the owner's private chat until they press Start.
            # Command handling still works; a missing optional menu must not block startup.
            logger.warning("Owner command menu unavailable; request details suppressed.")
        logger.info("Bot ready; sessions remain in memory only.")
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
            close_bot_session=False,
            tasks_concurrency_limit=100,
        )
    finally:
        try:
            await manager.close()
        finally:
            if store is not None:
                store.close()
            await bot.session.close()


def main() -> None:
    configure_logging()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        # Settings errors are fixed, input-free messages.
        logger.error("%s", exc)
        raise SystemExit(1) from None
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.error(
            "Bot stopped. Check the token, network, and BotFather group/inline settings. "
            "Also check owner configuration and authorization storage permissions. "
            "Request details suppressed."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
