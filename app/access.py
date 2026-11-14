"""Owner-managed authorization. Only access metadata is persisted, never updates or secrets."""

import asyncio
import logging
import os
import secrets
import sqlite3
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from pathlib import Path

from app.config import REQUEST_SECONDS, telegram_user_id
from app.sessions import SessionManager
from app.transport import Buttons, Unavailable

logger = logging.getLogger(__name__)
PAGE_SIZE = 20


class AccessUnavailable(Exception):
    """Authorization storage could not be checked or updated."""


@dataclass(frozen=True)
class AccessRecord:
    user_id: int
    status: str
    request_nonce: str | None


class AccessStore:
    def __init__(self, path: Path, bot_id: int):
        self.bot_id = bot_id
        self.connection: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            path.chmod(0o600)
            self.connection = sqlite3.connect(path, timeout=0.1)
            with self.database() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS access_users ("
                    "bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                    "status TEXT NOT NULL CHECK(status IN "
                    "('pending', 'approved', 'denied', 'revoked')), "
                    "request_nonce TEXT, created_at INTEGER NOT NULL, "
                    "updated_at INTEGER NOT NULL, PRIMARY KEY(bot_id, user_id))"
                )
        except (OSError, sqlite3.Error, AccessUnavailable):
            self.close()
            raise AccessUnavailable("Cannot open authorization storage.") from None

    @contextmanager
    def database(self):
        if self.connection is None:
            raise AccessUnavailable("Authorization storage is closed.")
        try:
            with self.connection:
                yield self.connection
        except sqlite3.Error:
            raise AccessUnavailable("Authorization storage is unavailable.") from None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def get(self, user_id: int) -> AccessRecord | None:
        with self.database() as db:
            row = db.execute(
                "SELECT user_id, status, request_nonce FROM access_users "
                "WHERE bot_id = ? AND user_id = ?",
                (self.bot_id, user_id),
            ).fetchone()
        return AccessRecord(*row) if row else None

    def request(self, user_id: int) -> AccessRecord | None:
        nonce = secrets.token_hex(8)
        now = int(time.time())
        with self.database() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO access_users VALUES (?, ?, 'pending', ?, ?, ?)",
                (self.bot_id, user_id, nonce, now, now),
            )
        return AccessRecord(user_id, "pending", nonce) if cursor.rowcount else None

    def decide(self, user_id: int, nonce: str, status: str) -> bool:
        with self.database() as db:
            cursor = db.execute(
                "UPDATE access_users SET status = ?, request_nonce = NULL, updated_at = ? "
                "WHERE bot_id = ? AND user_id = ? AND status = 'pending' AND request_nonce = ?",
                (status, int(time.time()), self.bot_id, user_id, nonce),
            )
        return bool(cursor.rowcount)

    def set_status(self, user_id: int, status: str) -> None:
        now = int(time.time())
        with self.database() as db:
            db.execute(
                "INSERT INTO access_users VALUES (?, ?, ?, NULL, ?, ?) "
                "ON CONFLICT(bot_id, user_id) DO UPDATE SET status = excluded.status, "
                "request_nonce = NULL, updated_at = excluded.updated_at",
                (self.bot_id, user_id, status, now, now),
            )

    def page(self, status: str, page: int) -> tuple[list[AccessRecord], bool]:
        with self.database() as db:
            rows = db.execute(
                "SELECT user_id, status, request_nonce FROM access_users "
                "WHERE bot_id = ? AND status = ? ORDER BY user_id LIMIT ? OFFSET ?",
                (self.bot_id, status, PAGE_SIZE + 1, page * PAGE_SIZE),
            ).fetchall()
        return [AccessRecord(*row) for row in rows[:PAGE_SIZE]], len(rows) > PAGE_SIZE


class AccessService:
    def __init__(self, store: AccessStore, owner_id: int, manager: SessionManager):
        self.store = store
        self.owner_id = owner_id
        self.manager = manager
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

    def lock(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def allowed(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return True
        record = self.store.get(user_id)
        return record is not None and record.status == "approved"

    async def send(self, chat_id: int, text: str, buttons: Buttons = ()) -> bool:
        try:
            await asyncio.wait_for(
                self.manager.transport.send(chat_id, text, buttons), REQUEST_SECONDS
            )
            return True
        except (Unavailable, TimeoutError, OSError):
            logger.warning("Access notification failed; request details suppressed.")
            return False

    async def delete_submission(self, user_id: int, message_id: int) -> None:
        try:
            await asyncio.wait_for(
                self.manager.transport.delete(user_id, message_id), REQUEST_SECONDS
            )
        except (Unavailable, TimeoutError, OSError):
            logger.warning("Restricted message cleanup failed; request details suppressed.")

    async def screen(self, user_id: int) -> None:
        record = self.store.get(user_id)
        if record is None:
            await self.send(
                user_id,
                "This bot requires owner approval. Do not send authentication secrets "
                "before approval. You can request access below. Use /whoami to see your ID.",
                (("Request access", "access:request"),),
            )
        elif record.status == "pending":
            await self.send(user_id, "Your access request is pending owner approval.")
        elif record.status in ("denied", "revoked"):
            await self.send(user_id, f"Your access is {record.status}. Contact the bot owner.")
        else:
            await self.manager.welcome(user_id, user_id)

    @staticmethod
    def decision_buttons(record: AccessRecord) -> Buttons:
        return tuple(
            (label, f"access:{action}:{record.user_id}:{record.request_nonce}")
            for label, action in (("Approve", "approve"), ("Deny", "deny"))
        )

    async def request(self, user_id: int, display_name: str) -> bool:
        async with self.lock(user_id):
            if user_id == self.owner_id:
                return False
            record = self.store.request(user_id)
        if record is None:
            await self.screen(user_id)
            return False
        await self.send(
            self.owner_id,
            f"Access request from {escape(display_name[:128])}\n"
            f"Telegram user ID: <code>{user_id}</code>",
            self.decision_buttons(record),
        )
        await self.screen(user_id)
        return True

    async def change(
        self, actor_id: int, user_id: int, status: str, nonce: str | None = None
    ) -> bool:
        if actor_id != self.owner_id or user_id == self.owner_id:
            return False
        async with self.lock(user_id):
            try:
                if nonce is not None:
                    if status not in ("approved", "denied"):
                        return False
                    if not self.store.decide(user_id, nonce, status):
                        return False
                else:
                    if status not in ("approved", "revoked"):
                        return False
                    self.store.set_status(user_id, status)
            finally:
                if status == "revoked":
                    # Also stop a live session if persistence fails; do not report success.
                    await self.manager.cancel(user_id, user_id)
        await self.send(user_id, f"Your access is now {status}.")
        return True

    async def listing(self, actor_id: int, kind: str, page: int = 0) -> bool:
        if actor_id != self.owner_id or kind not in ("requests", "users"):
            return False
        if not 0 <= page <= 1_000_000:
            return False
        records, more = self.store.page("pending" if kind == "requests" else "approved", page)
        title = (
            "Pending requests" if kind == "requests" else "Approved users (owner always allowed)"
        )
        text = f"{title} · Page {page + 1}\n" + (
            "\n".join(f"<code>{record.user_id}</code>" for record in records) or "No entries."
        )
        buttons = []
        if kind == "requests":
            buttons.extend(
                (f"Review {record.user_id}", f"access:review:{record.user_id}")
                for record in records
            )
        if page:
            buttons.append(("Previous", f"access:{kind}:{page - 1}"))
        if more:
            buttons.append(("Next", f"access:{kind}:{page + 1}"))
        if kind == "users":
            text += "\nUse /revoke &lt;user_id&gt; to remove access."
        await self.send(actor_id, text, tuple(buttons))
        return True

    async def callback(self, actor_id: int, data: str) -> bool:
        if actor_id != self.owner_id or not data.startswith("access:"):
            return False
        parts = data.split(":")
        if len(parts) == 3 and parts[1] in ("requests", "users"):
            if not parts[2].isascii() or not parts[2].isdecimal():
                return False
            return await self.listing(actor_id, parts[1], int(parts[2]))
        if len(parts) not in (3, 4):
            return False
        try:
            user_id = telegram_user_id(parts[2])
        except ValueError:
            return False
        if parts[1] == "review" and len(parts) == 3:
            record = self.store.get(user_id)
            if record is None or record.status != "pending":
                return False
            await self.send(
                actor_id, f"Access request: <code>{user_id}</code>", self.decision_buttons(record)
            )
            return True
        if parts[1] in ("approve", "deny") and len(parts) == 4:
            changed = await self.change(
                actor_id, user_id, "approved" if parts[1] == "approve" else "denied", parts[3]
            )
            if changed:
                await self.send(actor_id, f"Access {parts[1]} decision saved for {user_id}.")
            return changed
        return False
