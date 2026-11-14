import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from app.config import Settings
from app.main import ApplicationLogsOnly
from app.transport import RateLimited, TelegramTransport, Unavailable


def test_missing_token_and_invalid_timezone_do_not_quote_values(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "PRIVATE_INVALID_TOKEN")
    with pytest.raises(ValueError) as exc:
        Settings.from_env()
    assert "PRIVATE_INVALID_TOKEN" not in str(exc.value)
    monkeypatch.setenv("BOT_TOKEN", "123456:OFFLINE_TEST_TOKEN")
    monkeypatch.setenv("DISPLAY_TIMEZONE", "PRIVATE_INVALID_TIMEZONE")
    with pytest.raises(ValueError) as exc:
        Settings.from_env()
    assert "PRIVATE_INVALID_TIMEZONE" not in str(exc.value)


def test_credentials_hidden_and_owner_config_loaded(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:OFFLINE_TEST_TOKEN")
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Asia/Colombo")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "11")
    settings = Settings.from_env()
    assert settings.bot_token not in repr(settings)
    assert settings.owner_telegram_id == 11


@pytest.mark.parametrize("owner", [None, "", "@owner", "-11", "0", "1.5", "PRIVATE_ID", str(2**52)])
def test_owner_id_required_and_errors_do_not_echo_input(monkeypatch, owner):
    monkeypatch.setenv("BOT_TOKEN", "123456:OFFLINE_TEST_TOKEN")
    if owner is None:
        monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    else:
        monkeypatch.setenv("OWNER_TELEGRAM_ID", owner)
    with pytest.raises(ValueError, match="OWNER_TELEGRAM_ID") as exc:
        Settings.from_env()
    assert "PRIVATE_ID" not in str(exc.value)


def test_database_path_defaults_and_requires_persistent_file(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:OFFLINE_TEST_TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "11")
    monkeypatch.delenv("AUTH_DB_PATH", raising=False)
    assert Settings.from_env().auth_db_path == Path("data/access.sqlite3")
    for path in ("", ":memory:"):
        monkeypatch.setenv("AUTH_DB_PATH", path)
        with pytest.raises(ValueError, match="AUTH_DB_PATH"):
            Settings.from_env()


def test_external_payload_logs_are_filtered():
    filter_ = ApplicationLogsOnly()
    record = logging.LogRecord("aiogram.event", logging.ERROR, "", 1, "secret", (), None)
    assert not filter_.filter(record)
    record.name = "app.sessions"
    assert filter_.filter(record)


async def test_transport_redacts_sensitive_error_and_honors_retry_after():
    method = SendMessage(chat_id=11, text="PRIVATE_SECRET_OR_CODE")
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramBadRequest(
                method=method,
                message="PRIVATE_ERROR_BODY",
            )
        )
    )
    transport = TelegramTransport(bot)
    with pytest.raises(Unavailable) as exc:
        await transport.send(11, "PRIVATE_SECRET_OR_CODE")
    assert "PRIVATE" not in str(exc.value)
    bot.send_message.side_effect = TelegramRetryAfter(
        method=method, message="limited", retry_after=9
    )
    with pytest.raises(RateLimited) as exc:
        await transport.send(11, "PRIVATE_SECRET_OR_CODE")
    assert exc.value.retry_after == 9


async def test_transport_idempotent_deletion_and_same_message_edit():
    bot = SimpleNamespace(
        delete_message=AsyncMock(
            side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=11, message_id=1),
                message="Bad Request: message to delete not found",
            )
        ),
        edit_message_text=AsyncMock(
            side_effect=TelegramBadRequest(
                method=EditMessageText(chat_id=11, message_id=1, text="code"),
                message="Bad Request: message is not modified",
            )
        ),
    )
    transport = TelegramTransport(bot)
    await transport.delete(11, 1)
    await transport.edit(11, 1, "code", ())
