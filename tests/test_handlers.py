from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from app.handlers import make_router
from tests.conftest import DUMMY_SECRET, settle


def message_update(user=11, chat=None, chat_type="private", text=DUMMY_SECRET, update_id=1):
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1700000010,
                "chat": {"id": user if chat is None else chat, "type": chat_type},
                "from": {"id": user, "is_bot": False, "first_name": "Test"},
                "text": text,
            },
        }
    )


def dispatcher(manager):
    dp = Dispatcher(disable_fsm=True)
    dp.include_router(make_router(manager))
    bot = Bot("123456:TEST_TOKEN_FOR_OFFLINE_UNIT_TESTS")
    bot.session = AsyncMock(return_value=True)
    return dp, bot


async def test_real_dispatcher_accepts_private_secrets_and_rejects_groups(environment):
    manager, clock, transport = environment
    dp, bot = dispatcher(manager)
    await dp.feed_update(bot, message_update(chat=-99, chat_type="group"))
    await dp.feed_update(bot, message_update(chat=22))
    await settle()
    assert not manager.sessions
    assert not transport.sent
    await dp.feed_update(bot, message_update())
    await settle()
    assert 11 in manager.sessions
    assert (11, 1) in transport.deleted


async def test_real_callback_rejects_foreign_sender_then_allows_owner(environment):
    manager, clock, transport = environment
    dp, bot = dispatcher(manager)
    await dp.feed_update(bot, message_update())
    await settle()
    session = manager.sessions[11]

    def callback(user):
        return Update.model_validate(
            {
                "update_id": 10 + user,
                "callback_query": {
                    "id": "test-query",
                    "chat_instance": "test-chat",
                    "from": {"id": user, "is_bot": False, "first_name": "Test"},
                    "data": f"stop:{session.token}",
                    "message": {
                        "message_id": session.code_message,
                        "date": 1700000010,
                        "chat": {"id": 11, "type": "private"},
                        "text": "code display",
                    },
                },
            }
        )

    await dp.feed_update(bot, callback(22))
    assert 11 in manager.sessions
    await dp.feed_update(bot, callback(11))
    await settle()
    assert not manager.sessions
    assert not transport.messages


async def test_commands_with_secret_arguments_are_deleted(environment):
    manager, clock, transport = environment
    dp, bot = dispatcher(manager)
    await dp.feed_update(bot, message_update(text=f"/start {DUMMY_SECRET}"))
    await settle()
    assert (11, 1) in transport.deleted
    assert manager.sessions[11].config is None
    assert all(DUMMY_SECRET not in text for _, _, text in transport.sent)


async def test_onboarding_and_prompt_button(environment):
    manager, clock, transport = environment
    dp, bot = dispatcher(manager)
    await dp.feed_update(bot, message_update(text="/start"))
    await settle()
    text = transport.sent[0][2]
    assert "five minutes" in text
    assert "not end-to-end" in text
    await dp.feed_update(bot, message_update(text="/generate", update_id=2))
    await settle()
    assert manager.sessions[11].config is None
    await clock.advance(60)
    assert not manager.sessions
    assert (11, 2) in transport.deleted
    # General onboarding history remains; the session prompt is removed.
    assert len(transport.messages) == 1
