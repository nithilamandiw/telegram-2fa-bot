from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SetMyCommands

from app import main as entrypoint
from app.access import AccessUnavailable
from app.config import Settings


@pytest.mark.parametrize("groups,inline", [(False, False), (True, False), (False, True)])
async def test_startup_enforces_botfather_settings_and_always_closes(
    monkeypatch, tmp_path, groups, inline
):
    bot = SimpleNamespace(
        get_me=AsyncMock(
            return_value=SimpleNamespace(
                can_join_groups=groups,
                supports_inline_queries=inline,
            )
        ),
        delete_webhook=AsyncMock(),
        set_my_commands=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    dp = SimpleNamespace(include_router=lambda router: None, start_polling=AsyncMock())
    monkeypatch.setattr(entrypoint, "Bot", lambda *args, **kwargs: bot)
    monkeypatch.setattr(entrypoint, "Dispatcher", lambda **kwargs: dp)
    settings = Settings(
        "123456:OFFLINE_TEST_TOKEN", ZoneInfo("Asia/Colombo"), 11, tmp_path / "access.sqlite3"
    )
    if groups or inline:
        with pytest.raises(ValueError, match="BotFather"):
            await entrypoint.run(settings)
        dp.start_polling.assert_not_called()
    else:
        await entrypoint.run(settings)
        bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
        assert dp.start_polling.call_args.kwargs["allowed_updates"] == ["message", "callback_query"]
    bot.session.close.assert_awaited_once()


def test_unexpected_startup_failure_does_not_print_exception(monkeypatch, caplog):
    monkeypatch.setenv("BOT_TOKEN", "123456:OFFLINE_TEST_TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "11")
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: None)

    async def fail(settings):
        raise RuntimeError("PRIVATE_NETWORK_REQUEST_WITH_BOT_TOKEN")

    monkeypatch.setattr(entrypoint, "run", fail)
    with pytest.raises(SystemExit) as exc:
        entrypoint.main()
    assert exc.value.code == 1
    assert "PRIVATE_NETWORK_REQUEST" not in caplog.text
    assert "Request details suppressed" in caplog.text


async def test_database_failure_closes_bot_before_polling(monkeypatch, tmp_path):
    bot = SimpleNamespace(get_me=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(entrypoint, "Bot", lambda *args, **kwargs: bot)
    path = tmp_path / "blocked"
    path.write_text("not a directory")
    settings = Settings(
        "123456:OFFLINE_TEST_TOKEN", ZoneInfo("Asia/Colombo"), 11, path / "access.sqlite3"
    )
    with pytest.raises(AccessUnavailable):
        await entrypoint.run(settings)
    bot.get_me.assert_not_awaited()
    bot.session.close.assert_awaited_once()


async def test_owner_menu_not_available_does_not_block_first_start(monkeypatch, tmp_path):
    error = TelegramBadRequest(method=SetMyCommands(commands=[]), message="PRIVATE_CHAT_DETAIL")
    bot = SimpleNamespace(
        get_me=AsyncMock(
            return_value=SimpleNamespace(can_join_groups=False, supports_inline_queries=False)
        ),
        delete_webhook=AsyncMock(),
        set_my_commands=AsyncMock(side_effect=[True, error]),
        session=SimpleNamespace(close=AsyncMock()),
    )
    dp = SimpleNamespace(include_router=lambda router: None, start_polling=AsyncMock())
    monkeypatch.setattr(entrypoint, "Bot", lambda *args, **kwargs: bot)
    monkeypatch.setattr(entrypoint, "Dispatcher", lambda **kwargs: dp)
    settings = Settings(
        "123456:OFFLINE_TEST_TOKEN", ZoneInfo("Asia/Colombo"), 11, tmp_path / "access.sqlite3"
    )
    await entrypoint.run(settings)
    dp.start_polling.assert_awaited_once()
    assert bot.set_my_commands.call_args.kwargs["scope"].chat_id == 11
    bot.session.close.assert_awaited_once()
