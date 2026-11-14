from unittest.mock import AsyncMock

import pytest

from app import owner_id
from tests.conftest import DUMMY_SECRET
from tests.test_handlers import message_update


async def test_owner_lookup_only_returns_ids_from_private_whoami(monkeypatch):
    bot = AsyncMock()
    bot.__aenter__.return_value = bot
    bot.get_updates.return_value = [
        message_update(user=22, text="/whoami"),
        message_update(user=22, text="/whoami"),
        message_update(user=33, text="/whoami", chat=-99, chat_type="group"),
        message_update(user=44, text="/whoami", chat=55),
        message_update(user=66, text=DUMMY_SECRET),
        message_update(user=77, text=f"/whoami {DUMMY_SECRET}"),
    ]
    monkeypatch.setattr(owner_id, "Bot", lambda token: bot)
    assert await owner_id.lookup("OFFLINE_TOKEN") == [22]
    bot.get_updates.assert_awaited_once_with(limit=100, timeout=0, request_timeout=15)
    bot.__aexit__.assert_awaited_once()


def test_owner_lookup_errors_never_print_token_or_network_details(monkeypatch, capsys):
    monkeypatch.setenv("BOT_TOKEN", "123456:PRIVATE_BOT_TOKEN")
    monkeypatch.setattr(owner_id, "configure_logging", lambda: None)
    monkeypatch.setattr(owner_id, "lookup", AsyncMock(side_effect=RuntimeError("PRIVATE_REQUEST")))
    with pytest.raises(SystemExit):
        owner_id.main()
    assert "PRIVATE" not in capsys.readouterr().out
