import asyncio
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest_asyncio

from app.access import AccessService, AccessStore
from app.sessions import Clock, SessionManager
from app.transport import Buttons, Unavailable

DUMMY_SECRET = "JBSWY3DPEHPK3PXP"


async def settle():
    # wait_for, cancellation, and task callbacks each require an event-loop turn.
    for _ in range(40):
        await asyncio.sleep(0)


class FakeClock(Clock):
    def __init__(self):
        self.now = 0.0
        self.wall = 1_700_000_010.0  # Exactly on a 30-second boundary.
        self.waiters = []

    def monotonic(self):
        return self.now

    def unix(self):
        return self.wall

    async def sleep(self, seconds):
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        entry = (self.now + seconds, future)
        self.waiters.append(entry)
        try:
            await future
        finally:
            self.waiters.remove(entry)

    async def advance(self, seconds, wall_seconds=None):
        self.now += seconds
        self.wall += seconds if wall_seconds is None else wall_seconds
        for deadline, future in list(self.waiters):
            if deadline <= self.now and not future.done():
                future.set_result(None)
        await settle()


@dataclass
class StoredMessage:
    text: str
    buttons: Buttons


class FakeTransport:
    def __init__(self):
        self.messages = {}
        self.sent = []
        self.edits = []
        self.deleted = []
        self.next_id = 100
        self.delete_failures = set()
        self.send_error = None
        self.edit_errors = []
        self.block_edit = False
        self.edit_started = asyncio.Event()
        self.edit_cancelled = asyncio.Event()

    async def send(self, chat_id, text, buttons=()):
        if self.send_error:
            raise self.send_error
        self.next_id += 1
        mid = self.next_id
        self.messages[chat_id, mid] = StoredMessage(text, buttons)
        self.sent.append((chat_id, mid, text))
        return mid

    async def edit(self, chat_id, message_id, text, buttons):
        self.edit_started.set()
        if self.block_edit:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.edit_cancelled.set()
                raise
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.messages[chat_id, message_id] = StoredMessage(text, buttons)
        self.edits.append((chat_id, message_id, text))

    async def delete(self, chat_id, message_id):
        if (chat_id, message_id) in self.delete_failures:
            raise Unavailable("simulated private payload must never be logged")
        self.deleted.append((chat_id, message_id))
        self.messages.pop((chat_id, message_id), None)


@pytest_asyncio.fixture
async def environment():
    clock = FakeClock()
    transport = FakeTransport()
    manager = SessionManager(transport, ZoneInfo("Asia/Colombo"), clock)
    yield manager, clock, transport
    await manager.close()
    await settle()
    assert not manager.sessions
    assert not manager._tasks


@pytest_asyncio.fixture
async def access(environment, tmp_path):
    manager, _, _ = environment
    store = AccessStore(tmp_path / "access.sqlite3", 123456)
    yield AccessService(store, 11, manager)
    store.close()
