from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app import main as entrypoint
from app.config import Settings


@pytest.mark.parametrize("groups,inline", [(False, False), (True, False), (False, True)])
async def test_startup_enforces_botfather_settings_and_always_closes(monkeypatch, groups, inline):
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
    settings = Settings("123456:OFFLINE_TEST_TOKEN", ZoneInfo("Asia/Colombo"))
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
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: None)

    async def fail(settings):
        raise RuntimeError("PRIVATE_NETWORK_REQUEST_WITH_BOT_TOKEN")

    monkeypatch.setattr(entrypoint, "run", fail)
    with pytest.raises(SystemExit) as exc:
        entrypoint.main()
    assert exc.value.code == 1
    assert "PRIVATE_NETWORK_REQUEST" not in caplog.text
    assert "Request details suppressed" in caplog.text
