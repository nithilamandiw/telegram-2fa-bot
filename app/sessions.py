"""Memory-only sessions. Expiry never waits for a user lock or an API request."""

import asyncio
import logging
import math
import secrets
import time
import weakref
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from app.config import DISPLAY_INTERVAL_SECONDS, PROMPT_SECONDS, REQUEST_SECONDS, SESSION_SECONDS
from app.totp_service import InvalidSecret, TOTPConfig, generate, parse_secret
from app.transport import Buttons, RateLimited, Transport, Unavailable

logger = logging.getLogger(__name__)

ONBOARDING = (
    "<b>Private five-minute 2FA sessions</b>\n\n"
    "Send a setup secret to generate codes for five minutes. Your session is available "
    "only in this private chat. Secrets stay in the bot's memory and are never saved.\n\n"
    "Telegram and the running bot process your secret. Bot chats are not end-to-end "
    "encrypted. The bot attempts to delete your secret immediately and session messages "
    "when finished. Outages or crashes can prevent deletion; notifications and screenshots "
    "may remain. Python cannot guarantee forensic memory erasure.\n\n"
    "Keep your recovery codes elsewhere. Use /cancel or Stop to finish early."
)
PROMPT = (
    "Send your Base32 setup secret or an <code>otpauth://totp/...</code> link here. "
    "Do not include it in a command. This prompt expires in one minute. /cancel to cancel."
)


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def unix(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0, seconds))


@dataclass(repr=False)
class Session:
    user_id: int
    chat_id: int
    token: str
    deadline: float
    ends_at: float
    config: TOTPConfig | None = field(default=None, repr=False)
    messages: set[int] = field(default_factory=set)
    code_message: int | None = None
    last_window: int | None = None
    last_display: tuple[int, int, int] | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)
    render_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    manual_task: asyncio.Task | None = None


class SessionManager:
    def __init__(self, transport: Transport, timezone: ZoneInfo, clock: Clock | None = None):
        self.transport = transport
        self.timezone = timezone
        self.clock = clock or Clock()
        self.sessions: dict[int, Session] = {}
        # Locks disappear when there is no caller holding/waiting on them.
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self._tasks: set[asyncio.Task] = set()
        self._notice_tasks: set[asyncio.Task] = set()
        self._notices: set[tuple[int, int]] = set()
        self.closing = False

    def _lock(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    @staticmethod
    def _private(user_id: int, chat_id: int) -> bool:
        return user_id > 0 and user_id == chat_id

    def _spawn(self, coroutine: Coroutine[Any, Any, Any], session: Session | None = None):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        if session is not None:
            session.tasks.add(task)

        def done(completed: asyncio.Task) -> None:
            self._tasks.discard(completed)
            if session is not None:
                session.tasks.discard(completed)
            # Never log exception objects/tracebacks: they may retain API payloads.
            if not completed.cancelled() and completed.exception() is not None:
                logger.error("Background operation failed; request details suppressed.")
                if session is not None:
                    self._end(session)

        task.add_done_callback(done)
        return task

    def _alive(self, session: Session) -> bool:
        return (
            not self.closing
            and self.sessions.get(session.user_id) is session
            and self.clock.monotonic() < session.deadline
        )

    def _new(self, user_id: int, chat_id: int, config: TOTPConfig | None = None) -> Session:
        old = self.sessions.get(user_id)
        if old is not None:
            self._end(old)
        duration = SESSION_SECONDS if config is not None else PROMPT_SECONDS
        session = Session(
            user_id,
            chat_id,
            secrets.token_urlsafe(12),
            self.clock.monotonic() + duration,
            self.clock.unix() + duration,
            config,
        )
        self.sessions[user_id] = session
        self._spawn(self._expire(session), session)
        return session

    def _end(self, session: Session) -> None:
        if self.sessions.get(session.user_id) is not session:
            return
        del self.sessions[session.user_id]
        # Release secret state BEFORE waiting on cancellation or network cleanup.
        session.config = None
        session.last_window = None
        session.last_display = None
        session.manual_task = None
        current = asyncio.current_task()
        for task in tuple(session.tasks):
            if task is not current:
                task.cancel()
        session.tasks.clear()
        ids = tuple(session.messages)
        session.messages.clear()
        session.code_message = None
        if ids:
            self._spawn(self._cleanup(session.chat_id, ids))

    async def _expire(self, session: Session) -> None:
        while self.sessions.get(session.user_id) is session:
            remaining = session.deadline - self.clock.monotonic()
            if remaining <= 0:
                self._end(session)
                return
            await self.clock.sleep(remaining)

    async def welcome(self, user_id: int, chat_id: int) -> None:
        if self._private(user_id, chat_id) and not self.closing:
            try:
                await asyncio.wait_for(
                    self.transport.send(chat_id, ONBOARDING, (("Generate codes", "generate"),)),
                    REQUEST_SECONDS,
                )
            except (Unavailable, TimeoutError, OSError):
                pass

    async def prompt(self, user_id: int, chat_id: int, message_id: int | None = None) -> bool:
        if not self._private(user_id, chat_id) or self.closing:
            return False
        async with self._lock(user_id):
            if self.closing:
                return False
            session = self._new(user_id, chat_id)
            if message_id is not None:
                session.messages.add(message_id)
            self._spawn(self._session_notice(session, PROMPT), session)
        return True

    async def submit(self, user_id: int, chat_id: int, message_id: int, raw: str) -> str:
        if not self._private(user_id, chat_id) or self.closing:
            return "rejected"
        async with self._lock(user_id):
            if self.closing:
                return "rejected"
            # Delete before parsing, so malformed secrets are not left in chat either.
            deleted = await self._delete(chat_id, message_id)
            if self.closing:
                return "rejected"
            if not deleted:
                raw = ""
                await self._temporary_notice(
                    chat_id,
                    "I could not confirm deletion of your secret message. No session was "
                    "started. Please delete that message manually before trying again.",
                )
                return "delete_failed"
            try:
                config = parse_secret(raw)
            except InvalidSecret:
                raw = ""
                session = self.sessions.get(user_id)
                if session is None or not self._alive(session):
                    session = self._new(user_id, chat_id)
                self._spawn(
                    self._session_notice(
                        session,
                        "Invalid or unsupported TOTP secret. Send a Base32 secret "
                        "or a supported TOTP link. /cancel to cancel.",
                    ),
                    session,
                )
                return "invalid"
            raw = ""
            session = self._new(user_id, chat_id, config)
            config = None
            # Return immediately; input handlers must not retain raw updates for five minutes.
            self._spawn(self._refresh_loop(session), session)
            return "accepted"

    async def control(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        token: str,
        action: str,
    ) -> bool:
        if not self._private(user_id, chat_id) or self.closing:
            return False
        async with self._lock(user_id):
            session = self.sessions.get(user_id)
            if session is None:
                return False
            if not self._alive(session):
                self._end(session)
                return False
            if (
                session.chat_id != chat_id
                or session.token != token
                or session.code_message != message_id
                or session.config is None
            ):
                return False
            if action == "stop":
                self._end(session)
                return True
            if action == "refresh":
                if session.manual_task is None or session.manual_task.done():
                    session.manual_task = self._spawn(self._render(session), session)
                return True
            return False

    async def cancel(self, user_id: int, chat_id: int, message_id: int | None = None) -> None:
        if not self._private(user_id, chat_id):
            return
        async with self._lock(user_id):
            session = self.sessions.get(user_id)
            if session is not None:
                if message_id is not None:
                    session.messages.add(message_id)
                self._end(session)
            elif message_id is not None:
                self._spawn(self._cleanup(chat_id, (message_id,)))

    def _remember(self, session: Session, message_id: int) -> None:
        if self._alive(session):
            session.messages.add(message_id)
            # Bound memory used by repeated invalid submissions/status messages.
            if len(session.messages) > 20:
                old = next(mid for mid in session.messages if mid != session.code_message)
                session.messages.remove(old)
                self._spawn(self._cleanup(session.chat_id, (old,)))
        else:
            self._spawn(self._cleanup(session.chat_id, (message_id,)))

    async def _session_notice(self, session: Session, text: str) -> None:
        if not self._alive(session):
            return
        try:
            mid = await asyncio.wait_for(
                self.transport.send(session.chat_id, text),
                min(REQUEST_SECONDS, session.deadline - self.clock.monotonic()),
            )
            self._remember(session, mid)
        except (Unavailable, TimeoutError, OSError):
            pass

    @staticmethod
    def _time_bar(remaining: int, total: int) -> str:
        filled = max(0, min(10, math.ceil(remaining * 10 / total)))
        return "▓" * filled + "░" * (10 - filled)

    def _display(self, session: Session) -> tuple[str, tuple[int, int, int]]:
        # Synchronous generation leaves no local config object suspended across an await.
        assert session.config is not None
        now = self.clock.unix()
        code, expires = generate(session.config, now)
        code_remaining = max(0, min(session.config.period, math.ceil(expires - now)))
        session_remaining = max(0, math.ceil(session.deadline - self.clock.monotonic()))
        minutes, seconds = divmod(session_remaining, 60)
        code_end = datetime.fromtimestamp(expires, self.timezone).strftime("%H:%M:%S")
        session_end = datetime.fromtimestamp(session.ends_at, self.timezone).strftime("%H:%M:%S")
        text = (
            f"Your 2FA code: <code>{code}</code>\n"
            f"Refresh in: ~{code_remaining}s\n"
            f"<code>{self._time_bar(code_remaining, session.config.period)}</code>\n\n"
            f"Session remaining: ~{minutes:02d}:{seconds:02d}\n"
            f"<code>{self._time_bar(session_remaining, SESSION_SECONDS)}</code>\n\n"
            f"Code valid until: {code_end}\nSession ends at: {session_end}\n"
            f"Timezone: {escape(str(self.timezone))}\n\n"
            f"Timers update about every {DISPLAY_INTERVAL_SECONDS}s · Stop ends it early."
        )
        return text, (int(now) // session.config.period, code_remaining, session_remaining)

    async def _render(self, session: Session) -> None:
        async with session.render_lock:
            while self._alive(session) and session.config is not None:
                text, display = self._display(session)
                if display == session.last_display and session.code_message is not None:
                    return
                buttons: Buttons = (
                    ("Refresh", f"refresh:{session.token}"),
                    ("Stop", f"stop:{session.token}"),
                )
                try:
                    timeout = min(REQUEST_SECONDS, session.deadline - self.clock.monotonic())
                    if timeout <= 0:
                        self._end(session)
                        return
                    if session.code_message is None:
                        mid = await asyncio.wait_for(
                            self.transport.send(session.chat_id, text, buttons),
                            timeout,
                        )
                        self._remember(session, mid)
                        if self._alive(session):
                            session.code_message = mid
                    else:
                        await asyncio.wait_for(
                            self.transport.edit(
                                session.chat_id,
                                session.code_message,
                                text,
                                buttons,
                            ),
                            timeout,
                        )
                    text = ""
                    if self._alive(session):
                        session.last_window = display[0]
                        session.last_display = display
                    return
                except RateLimited as exc:
                    delay = max(0.1, exc.retry_after)
                    text = ""
                    remaining = session.deadline - self.clock.monotonic()
                    if delay >= remaining:
                        self._end(session)
                        return
                    await self.clock.sleep(delay)
                    # Loop regenerates using current Unix time, never replays old payloads.
                except (Unavailable, TimeoutError, OSError):
                    self._end(session)
                    return

    async def _refresh_loop(self, session: Session) -> None:
        while self._alive(session) and session.config is not None:
            await self._render(session)
            if not self._alive(session) or session.config is None:
                return
            now = self.clock.unix()
            if int(now) // session.config.period != session.last_window:
                # A successful but slow API call may have crossed a rollover boundary.
                continue
            next_rollover = (int(now) // session.config.period + 1) * session.config.period
            next_tick = (int(now) // DISPLAY_INTERVAL_SECONDS + 1) * DISPLAY_INTERVAL_SECONDS
            delay = min(
                next_tick - now, next_rollover - now, session.deadline - self.clock.monotonic()
            )
            await self.clock.sleep(max(0.01, delay))

    async def _delete(self, chat_id: int, message_id: int) -> bool:
        try:
            await asyncio.wait_for(self.transport.delete(chat_id, message_id), REQUEST_SECONDS)
            return True
        except (Unavailable, TimeoutError, OSError):
            return False

    async def _cleanup(self, chat_id: int, ids: tuple[int, ...]) -> None:
        failed = False
        for mid in ids:
            try:
                await asyncio.wait_for(self.transport.delete(chat_id, mid), REQUEST_SECONDS)
            except RateLimited as exc:
                # Never retry sooner than Telegram allows; do not retain cleanup forever.
                if exc.retry_after <= 2 and not self.closing:
                    await self.clock.sleep(max(0.1, exc.retry_after))
                    failed = not await self._delete(chat_id, mid) or failed
                else:
                    failed = True
            except (Unavailable, TimeoutError, OSError):
                failed = True
        if failed and not self.closing:
            await self._temporary_notice(
                chat_id,
                "Session ended. Some messages could not be deleted. "
                "Please remove any remaining session messages manually.",
            )

    async def _temporary_notice(self, chat_id: int, text: str) -> None:
        if self.closing:
            return
        try:
            mid = await asyncio.wait_for(self.transport.send(chat_id, text), REQUEST_SECONDS)
        except (Unavailable, TimeoutError, OSError):
            return
        if self.closing:
            await self._delete(chat_id, mid)
            return
        self._notices.add((chat_id, mid))
        task = self._spawn(self._expire_notice(chat_id, mid))
        self._notice_tasks.add(task)
        task.add_done_callback(self._notice_tasks.discard)

    async def _expire_notice(self, chat_id: int, mid: int) -> None:
        try:
            await self.clock.sleep(PROMPT_SECONDS)
        finally:
            await self._delete(chat_id, mid)
            self._notices.discard((chat_id, mid))

    async def close(self) -> None:
        self.closing = True
        for session in tuple(self.sessions.values()):
            self._end(session)
        for task in tuple(self._notice_tasks):
            task.cancel()
        # A newly created notice task may be cancelled before entering its finally block.
        for chat_id, mid in tuple(self._notices):
            self._spawn(self._cleanup(chat_id, (mid,)))
        self._notices.clear()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
