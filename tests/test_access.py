import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Update

from app.access import AccessStore, AccessUnavailable
from app.transport import Unavailable, keyboard
from tests.conftest import DUMMY_SECRET, settle
from tests.test_handlers import dispatcher, message_update


def callback_update(user, data, chat=None, message_id=100, chat_type="private"):
    return Update.model_validate(
        {
            "update_id": 1000,
            "callback_query": {
                "id": "access-query",
                "chat_instance": "chat-instance",
                "from": {"id": user, "is_bot": False, "first_name": "<Test & User>"},
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": 1700000010,
                    "chat": {"id": user if chat is None else chat, "type": chat_type},
                    "text": "Access screen",
                },
            },
        }
    )


def test_store_persistence_namespaces_and_metadata_only(tmp_path):
    path = tmp_path / "private" / "access.sqlite3"
    store = AccessStore(path, 123)
    record = store.request(22)
    assert record.status == "pending"
    assert store.request(22) is None
    assert not store.decide(22, "forged", "approved")
    assert store.decide(22, record.request_nonce, "approved")
    assert not store.decide(22, record.request_nonce, "denied")
    store.close()
    store = AccessStore(path, 123)
    assert store.get(22).status == "approved"
    assert store.get(22).request_nonce is None
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(access_users)")}
    assert columns == {"bot_id", "user_id", "status", "request_nonce", "created_at", "updated_at"}
    store.close()
    store = AccessStore(path, 456)
    assert store.get(22) is None
    store.close()
    assert path.stat().st_mode & 0o777 == 0o600


def test_store_open_and_runtime_failures_are_safe(tmp_path):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("PRIVATE_STORAGE_DETAIL")
    with pytest.raises(AccessUnavailable) as exc:
        AccessStore(blocked / "access.sqlite3", 123)
    assert "PRIVATE_STORAGE_DETAIL" not in str(exc.value)
    path = tmp_path / "bad.sqlite3"
    path.write_text("PRIVATE_DATABASE_CONTENT")
    with pytest.raises(AccessUnavailable) as exc:
        AccessStore(path, 123)
    assert "PRIVATE_DATABASE_CONTENT" not in str(exc.value)


@pytest.mark.parametrize("text", [DUMMY_SECRET, "/generate", "/approve 22", "/revoke 11"])
async def test_unauthorized_messages_never_reach_totp(environment, access, monkeypatch, text):
    manager, _, transport = environment
    submit = AsyncMock()
    prompt = AsyncMock()
    monkeypatch.setattr(manager, "submit", submit)
    monkeypatch.setattr(manager, "prompt", prompt)
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, message_update(user=22, text=text))
    submit.assert_not_awaited()
    prompt.assert_not_awaited()
    assert not manager.sessions
    assert (22, 1) in transport.deleted
    assert access.store.get(22) is None
    assert all(chat == 22 for chat, _, _ in transport.sent)
    assert all(DUMMY_SECRET not in message for _, _, message in transport.sent)


async def test_whoami_and_request_screen_do_not_implicitly_request(environment, access):
    manager, _, transport = environment
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, message_update(user=22, text="/whoami"))
    assert "22" in transport.sent[-1][2]
    await dp.feed_update(bot, message_update(user=22, text="/start"))
    screen = transport.messages[22, transport.sent[-1][1]]
    assert screen.buttons == (("Request access", "access:request"),)
    assert access.store.get(22) is None
    assert not manager.sessions


async def test_requests_owner_decision_and_stale_callbacks(environment, access):
    manager, _, transport = environment
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, callback_update(22, "access:request"))
    record = access.store.get(22)
    owner_message = next(text for chat, _, text in transport.sent if chat == 11)
    assert "&lt;Test &amp; User&gt;" in owner_message
    assert "<Test & User>" not in owner_message
    count = len([chat for chat, _, _ in transport.sent if chat == 11])
    await dp.feed_update(bot, callback_update(22, "access:request"))
    assert len([chat for chat, _, _ in transport.sent if chat == 11]) == count
    assert access.store.get(22).request_nonce == record.request_nonce
    approve = f"access:approve:22:{record.request_nonce}"
    await dp.feed_update(bot, callback_update(22, approve))
    await dp.feed_update(bot, callback_update(11, approve, chat=22))
    await dp.feed_update(bot, callback_update(11, approve, chat=-99, chat_type="group"))
    await dp.feed_update(bot, callback_update(11, "access:approve:22:forged"))
    assert access.store.get(22).status == "pending"
    await dp.feed_update(bot, callback_update(11, approve))
    assert access.allowed(22)
    await dp.feed_update(bot, message_update(user=22))
    await settle()
    assert 22 in manager.sessions
    await dp.feed_update(bot, message_update(text="/revoke 22", update_id=2))
    await settle()
    assert not access.allowed(22)
    assert 22 not in manager.sessions
    assert not any(
        chat == 22 and "Your 2FA code" in item.text
        for (chat, _), item in transport.messages.items()
    )
    await dp.feed_update(bot, callback_update(11, approve))
    await dp.feed_update(bot, callback_update(22, "access:request"))
    assert access.store.get(22).status == "revoked"
    await dp.feed_update(bot, message_update(text="/approve 22", update_id=3))
    assert access.allowed(22)
    assert not await access.change(11, 11, "revoked")
    assert access.allowed(11)


async def test_denial_blocks_requests_until_manual_approval(environment, access):
    manager, _, _ = environment
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, callback_update(22, "access:request"))
    nonce = access.store.get(22).request_nonce
    await dp.feed_update(bot, callback_update(11, f"access:deny:22:{nonce}"))
    assert access.store.get(22).status == "denied"
    await dp.feed_update(bot, callback_update(22, "access:request"))
    assert access.store.get(22).status == "denied"
    assert access.store.get(22).request_nonce is None
    await dp.feed_update(bot, message_update(text="/approve 22"))
    assert access.allowed(22)


async def test_notification_failure_keeps_request_and_decision(environment, access):
    _, _, transport = environment
    transport.send_error = Unavailable("PRIVATE_NOTIFICATION_PAYLOAD")
    assert await access.request(22, "Test")
    record = access.store.get(22)
    assert record.status == "pending"
    transport.send_error = None
    assert await access.listing(11, "requests")
    assert "22" in transport.sent[-1][2]
    assert await access.callback(11, "access:review:22")
    assert transport.messages[11, transport.sent[-1][1]].buttons == access.decision_buttons(record)
    transport.send_error = Unavailable("PRIVATE_NOTIFICATION_PAYLOAD")
    assert await access.change(11, 22, "approved", record.request_nonce)
    assert access.allowed(22)


async def test_pagination_and_owner_only_commands(environment, access):
    manager, _, transport = environment
    for user in range(100, 121):
        access.store.request(user)
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, message_update(text="/requests"))
    first = transport.messages[11, transport.sent[-1][1]]
    assert first.text.count("<code>") == 20
    assert ("Next", "access:requests:1") in first.buttons
    markup = keyboard(first.buttons)
    assert all(len(row) <= 2 for row in markup.inline_keyboard)
    await dp.feed_update(bot, callback_update(11, "access:requests:1"))
    second = transport.messages[11, transport.sent[-1][1]]
    assert second.text.count("<code>") == 1
    assert "120" in second.text
    assert second.buttons[-1] == ("Previous", "access:requests:0")
    assert not await access.listing(22, "requests")
    assert not await access.callback(22, "access:review:100")
    assert not await access.callback(11, "access:requests:-1")
    assert not await access.callback(11, "access:requests:1000001")
    for user in range(100, 121):
        access.store.set_status(user, "approved")
    await dp.feed_update(bot, message_update(text="/users"))
    assert transport.sent[-1][2].count("<code>") == 20
    assert "owner always allowed" in transport.sent[-1][2]


async def test_revoked_buttons_and_database_failure_cannot_start_or_refresh(environment, access):
    manager, _, transport = environment
    dp, bot = dispatcher(manager, access)
    access.store.set_status(22, "approved")
    await dp.feed_update(bot, message_update(user=22))
    await settle()
    session = manager.sessions[22]
    data = f"refresh:{session.token}"
    message_id = session.code_message
    await access.change(11, 22, "revoked")
    await settle()
    edits = len(transport.edits)
    await dp.feed_update(bot, callback_update(22, data, message_id=message_id))
    await dp.feed_update(bot, callback_update(22, "generate"))
    await settle()
    assert 22 not in manager.sessions
    assert len(transport.edits) == edits
    access.store.set_status(22, "approved")
    await dp.feed_update(bot, message_update(user=22, update_id=2))
    await settle()
    assert 22 in manager.sessions
    access.store.close()
    await dp.feed_update(bot, callback_update(22, "generate"))
    await settle()
    assert 22 not in manager.sessions
    await dp.feed_update(bot, message_update(user=22, update_id=3))
    assert (22, 3) in transport.deleted
    assert "could not be verified" in transport.sent[-1][2]


async def test_database_write_failure_does_not_grant_access(
    environment, access, monkeypatch, caplog
):
    manager, _, transport = environment
    dp, bot = dispatcher(manager, access)
    access.store.request(22)

    def fail(*args):
        raise AccessUnavailable("PRIVATE_DATABASE_PATH")

    monkeypatch.setattr(access.store, "set_status", fail)
    await dp.feed_update(bot, message_update(text="/approve 22"))
    assert not access.allowed(22)
    assert all("Access updated" not in text for _, _, text in transport.sent)
    assert "PRIVATE_DATABASE_PATH" not in caplog.text


async def test_revocation_cancels_in_flight_refresh(environment, access):
    manager, clock, transport = environment
    access.store.set_status(22, "approved")
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, message_update(user=22))
    await settle()
    transport.block_edit = True
    await clock.advance(5)
    assert transport.edit_started.is_set()
    assert await access.change(11, 22, "revoked")
    await settle()
    assert transport.edit_cancelled.is_set()
    assert 22 not in manager.sessions


async def test_approved_secrets_never_persist_in_access_database(environment, access):
    manager, _, _ = environment
    access.store.set_status(22, "approved")
    dp, bot = dispatcher(manager, access)
    await dp.feed_update(bot, message_update(user=22))
    await settle()
    rows = access.store.connection.execute("SELECT * FROM access_users").fetchall()
    assert rows[0][0:3] == (123456, 22, "approved")
    assert DUMMY_SECRET not in repr(rows)
    assert str(manager._display(manager.sessions[22])[0]) not in repr(rows)


async def test_revoke_serializes_with_submission_and_stops_background_refresh(
    environment, access, monkeypatch
):
    manager, clock, transport = environment
    dp, bot = dispatcher(manager, access)
    access.store.set_status(22, "approved")
    started = asyncio.Event()
    release = asyncio.Event()
    delete = transport.delete

    async def slow_delete(chat_id, message_id):
        if (chat_id, message_id) == (22, 1):
            started.set()
            await release.wait()
        await delete(chat_id, message_id)

    monkeypatch.setattr(transport, "delete", slow_delete)
    submission = asyncio.create_task(dp.feed_update(bot, message_update(user=22)))
    await started.wait()
    revocation = asyncio.create_task(access.change(11, 22, "revoked"))
    await settle()
    assert not revocation.done()
    release.set()
    await submission
    assert await revocation
    await settle()
    assert not access.allowed(22)
    assert 22 not in manager.sessions
    edits = len(transport.edits)
    await clock.advance(30)
    assert len(transport.edits) == edits
    await dp.feed_update(bot, message_update(user=22, update_id=3))
    assert 22 not in manager.sessions
