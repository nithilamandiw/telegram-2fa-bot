"""Run with python -m app.main or telegram-totp."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

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
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(make_router(manager))
    try:
        info = await bot.get_me()
        if info.can_join_groups or info.supports_inline_queries:
            raise ValueError("Disable group joining and inline mode through BotFather.")
        # Discard pending updates from downtime instead of processing old secret messages.
        # Such messages cannot be reliably cleaned after a crash without saved identifiers.
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="About private five-minute sessions"),
                BotCommand(command="generate", description="Generate temporary 2FA codes"),
                BotCommand(command="cancel", description="Stop and delete this session"),
                BotCommand(command="help", description="Privacy and usage information"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
        logger.info("Bot ready; sessions remain in memory only.")
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
            close_bot_session=False,
            tasks_concurrency_limit=100,
        )
    finally:
        await manager.close()
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
            "Request details suppressed."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
