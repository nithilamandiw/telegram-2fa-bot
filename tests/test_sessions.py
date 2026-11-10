import asyncio

import pytest

from app.sessions import SessionManager
from app.totp_service import generate, parse_secret
from app.transport import MessageGone, RateLimited, Unavailable
from tests.conftest import DUMMY_SECRET, settle


async def start(manager, user, mid=1, secret=DUMMY_SECRET):
    assert await manager.submit(user, user, mid, secret) == "accepted"
    await settle()
    return manager.sessions[user]


async def test_two_users_cannot_control_each_other(environment):
    manager, clock, transport = environment
    first = await start(manager, 11)
    second = await start(manager, 22)
    for action in ("refresh", "stop"):
        assert not await manager.control(22, 11, first.code_message, first.token, action)
        assert not await manager.control(22, 22, first.code_message, first.token, action)
        assert not await manager.control(11, 22, second.code_message, second.token, action)
    assert set(manager.sessions) == {11, 22}
    assert await manager.control(11, 11, first.code_message, first.token, "stop")
    await settle()
    assert first.config is None
    assert second.config is not None
    assert 22 in manager.sessions
    assert (22, second.code_message) in transport.messages


async def test_independent_expiry_and_no_generation_after_deadline(environment, monkeypatch):
    manager, clock, transport = environment
    first = await start(manager, 11)
    await clock.advance(40)
    second = await start(manager, 22)
    calls = []
    from app import sessions

    original = sessions.generate

    def checked(config, timestamp):
        calls.append(clock.monotonic())
        return original(config, timestamp)

    monkeypatch.setattr(sessions, "generate", checked)
    await clock.advance(260)
    assert 11 not in manager.sessions
    assert first.config is None
    assert 22 in manager.sessions
    second_mid = second.code_message
    await clock.advance(40)
    assert not manager.sessions
    assert second.config is None
    assert (22, second_mid) in transport.deleted
    assert all(t < 340 for t in calls)
    assert not transport.messages


async def test_refresh_does_not_extend_deadline_or_send_same_window(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    deadline = session.deadline
    for _ in range(5):
        assert await manager.control(11, 11, session.code_message, session.token, "refresh")
        await settle()
    assert not transport.edits
    assert session.deadline == deadline == 300
    await clock.advance(30)
    assert len(transport.edits) == 1
    assert transport.edits[0][2] != transport.sent[0][2]
    await clock.advance(270)
    assert session.config is None
    assert not manager.sessions


async def test_replacement_clears_previous_and_rejects_stale_callbacks(environment):
    manager, clock, transport = environment
    previous = await start(manager, 11)
    old_mid, old_token = previous.code_message, previous.token
    other = await start(manager, 22)
    await clock.advance(10)
    current = await start(manager, 11, mid=2)
    assert previous.config is None
    assert (11, old_mid) in transport.deleted
    assert current.deadline == 310
    assert manager.sessions[22] is other
    assert not await manager.control(11, 11, old_mid, old_token, "stop")
    assert not await manager.control(11, 11, current.code_message, old_token, "refresh")
    assert manager.sessions[11] is current


async def test_secret_deletion_failure_aborts_and_does_not_log(environment, caplog):
    manager, clock, transport = environment
    transport.delete_failures.add((11, 1))
    assert await manager.submit(11, 11, 1, DUMMY_SECRET) == "delete_failed"
    await settle()
    assert not manager.sessions
    assert not any("Your 2FA code" in text for _, _, text in transport.sent)
    assert DUMMY_SECRET not in caplog.text
    assert "simulated private payload" not in caplog.text
    await clock.advance(60)
    assert not transport.messages


async def test_invalid_secret_deleted_and_prompt_status_cleanup(environment):
    manager, clock, transport = environment
    await manager.prompt(11, 11)
    await settle()
    assert await manager.submit(11, 11, 1, "invalid secret!") == "invalid"
    await settle()
    assert (11, 1) in transport.deleted
    assert len(transport.messages) == 2
    await clock.advance(60)
    assert not manager.sessions
    assert not transport.messages


async def test_pending_prompt_is_deleted_when_session_starts(environment):
    manager, clock, transport = environment
    await manager.prompt(11, 11)
    await settle()
    prompt_mid = next(iter(transport.messages))[1]
    await start(manager, 11)
    assert (11, prompt_mid) in transport.deleted
    assert len(transport.messages) == 1


async def test_expiry_cancels_slow_api_request_without_waiting(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    mid = session.code_message
    transport.block_edit = True
    await clock.advance(30)
    assert transport.edit_started.is_set()
    await clock.advance(270)
    assert session.config is None
    assert not manager.sessions
    assert transport.edit_cancelled.is_set()
    assert (11, mid) in transport.deleted


async def test_retry_regenerates_code_and_does_not_replay_old_window(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    transport.edit_errors.append(RateLimited(35))
    await clock.advance(30)
    assert not transport.edits
    await clock.advance(35)
    expected = generate(parse_secret(DUMMY_SECRET), clock.unix())[0]
    assert f"<code>{expected}</code>" in transport.edits[-1][2]
    assert session.deadline == 300


async def test_rate_limit_cannot_keep_secret_past_expiry(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    transport.edit_errors.append(RateLimited(290))
    await clock.advance(30)
    assert session.config is None
    assert not manager.sessions
    assert not transport.edits


@pytest.mark.parametrize("error", [Unavailable(), MessageGone()])
async def test_inaccessible_display_ends_session(environment, error):
    manager, clock, transport = environment
    session = await start(manager, 11)
    transport.edit_errors.append(error)
    await clock.advance(30)
    assert session.config is None
    assert not manager.sessions


async def test_cleanup_failure_warns_without_secret_then_expires_notice(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    transport.delete_failures.add((11, session.code_message))
    await clock.advance(300)
    assert session.config is None
    assert "manually" in transport.sent[-1][2]
    notice = transport.sent[-1][1]
    await clock.advance(60)
    assert (11, notice) in transport.deleted


async def test_wall_clock_change_does_not_change_session_lifetime(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    await clock.advance(299, wall_seconds=-3600)
    assert session.config is not None
    await clock.advance(1, wall_seconds=0)
    assert session.config is None
    assert not manager.sessions


async def test_shutdown_cancels_all_sessions_and_restart_has_no_state(environment):
    manager, clock, transport = environment
    first = await start(manager, 11)
    second = await start(manager, 22)
    await manager.close()
    await settle()
    assert first.config is second.config is None
    assert not manager.sessions
    assert not transport.messages
    assert all(task.done() for task in manager._tasks)
    restarted = SessionManager(transport, manager.timezone, clock)
    assert not restarted.sessions
    assert not await restarted.control(11, 11, 1, first.token, "refresh")
    await restarted.close()


async def test_cancel_and_nonprivate_rejection(environment):
    manager, clock, transport = environment
    assert await manager.submit(11, -99, 1, DUMMY_SECRET) == "rejected"
    assert not await manager.prompt(11, 22)
    assert not transport.sent
    session = await start(manager, 11)
    await manager.cancel(11, 11, message_id=2)
    await settle()
    assert session.config is None
    assert (11, 2) in transport.deleted
    assert not transport.messages


async def test_blocked_bot_does_not_leave_secret_in_memory(environment):
    manager, clock, transport = environment
    transport.send_error = Unavailable()
    assert await manager.submit(11, 11, 1, DUMMY_SECRET) == "accepted"
    await settle()
    assert not manager.sessions


async def test_no_application_files_or_payload_logs(environment, tmp_path, monkeypatch, caplog):
    manager, clock, transport = environment
    monkeypatch.chdir(tmp_path)
    session = await start(manager, 11)
    code = generate(parse_secret(DUMMY_SECRET), clock.unix())[0]
    await clock.advance(300)
    assert session.config is None
    assert list(tmp_path.iterdir()) == []
    assert DUMMY_SECRET not in caplog.text
    assert code not in caplog.text


async def test_concurrent_inputs_keep_one_session_per_user(environment):
    manager, clock, transport = environment
    results = await asyncio.gather(
        manager.submit(11, 11, 1, DUMMY_SECRET),
        manager.submit(11, 11, 2, DUMMY_SECRET),
        manager.submit(22, 22, 1, DUMMY_SECRET),
    )
    await settle()
    assert results == ["accepted"] * 3
    assert set(manager.sessions) == {11, 22}
    assert len([entry for entry in transport.messages if entry[0] == 11]) == 1


async def test_prompt_waiting_on_lock_cannot_reopen_after_shutdown(environment):
    manager, clock, transport = environment
    lock = manager._lock(11)
    await lock.acquire()
    task = asyncio.create_task(manager.prompt(11, 11))
    await settle()
    await manager.close()
    lock.release()
    assert not await task
    assert not manager.sessions


async def test_shutdown_deletes_notice_even_before_its_expiry_task_starts(environment):
    manager, clock, transport = environment
    await manager._temporary_notice(11, "Temporary cleanup notice")
    mid = transport.sent[-1][1]
    await manager.close()
    assert (11, mid) in transport.deleted


async def test_expiry_cancels_manual_refresh_waiting_behind_slow_edit(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    transport.block_edit = True
    await clock.advance(30)
    assert await manager.control(11, 11, session.code_message, session.token, "refresh")
    manual = session.manual_task
    await settle()
    await clock.advance(270)
    assert session.config is None
    assert manual.cancelled()
    assert not manager.sessions


async def test_slow_successful_send_crossing_rollover_is_immediately_refreshed(environment):
    manager, clock, transport = environment
    send = transport.send
    ready = asyncio.Event()

    async def slow_first_send(chat_id, text, buttons=()):
        await ready.wait()
        return await send(chat_id, text, buttons)

    transport.send = slow_first_send
    assert await manager.submit(11, 11, 1, DUMMY_SECRET) == "accepted"
    await settle()
    await clock.advance(35)
    ready.set()
    await settle()
    session = manager.sessions[11]
    expected = generate(parse_secret(DUMMY_SECRET), clock.unix())[0]
    assert len(transport.edits) == 1
    assert f"<code>{expected}</code>" in transport.messages[11, session.code_message].text


async def test_countdown_updates_same_message_and_code_only_changes_on_rollover(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    mid = session.code_message
    initial = transport.messages[11, mid].text
    initial_code = generate(parse_secret(DUMMY_SECRET), clock.unix())[0]
    assert "Refresh in: ~30s" in initial
    assert "Session remaining: ~05:00" in initial
    assert initial.count("▓" * 10) == 2

    await clock.advance(5)
    updated = transport.messages[11, mid].text
    assert "Refresh in: ~25s" in updated
    assert "Session remaining: ~04:55" in updated
    assert f"<code>{initial_code}</code>" in updated
    assert "▓" * 9 + "░" in updated
    assert len(transport.sent) == 1

    await clock.advance(20)
    assert "Refresh in: ~5s" in transport.messages[11, mid].text
    await clock.advance(5)
    rolled = transport.messages[11, mid].text
    expected = generate(parse_secret(DUMMY_SECRET), clock.unix())[0]
    assert expected != initial_code
    assert f"<code>{expected}</code>" in rolled
    assert "Refresh in: ~30s" in rolled
    assert session.deadline == 300


async def test_manual_refresh_updates_remaining_time_without_extending_session(environment):
    manager, clock, transport = environment
    session = await start(manager, 11)
    mid = session.code_message
    await clock.advance(2)
    assert not transport.edits  # No per-second polling.
    assert await manager.control(11, 11, mid, session.token, "refresh")
    await settle()
    assert "Refresh in: ~28s" in transport.messages[11, mid].text
    assert "Session remaining: ~04:58" in transport.messages[11, mid].text
    assert session.deadline == 300
    await clock.advance(298)
    assert session.config is None
    assert (11, mid) in transport.deleted
    assert not transport.messages


async def test_countdown_uses_imported_period_and_monotonic_session_time(environment):
    manager, clock, transport = environment
    clock.wall += 30  # Start at a 60-second boundary.
    secret = f"otpauth://totp/test?secret={DUMMY_SECRET}&period=60"
    session = await start(manager, 11, secret=secret)
    mid = session.code_message
    assert "Refresh in: ~60s" in transport.messages[11, mid].text
    await clock.advance(5)
    assert "Refresh in: ~55s" in transport.messages[11, mid].text
    await clock.advance(5, wall_seconds=-3600)
    assert "Session remaining: ~04:50" in transport.messages[11, mid].text
    await clock.advance(290)
    assert session.config is None
    assert (11, mid) in transport.deleted
