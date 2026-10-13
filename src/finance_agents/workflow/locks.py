"""Event-loop-local keyed mutexes, retained only while held or awaited."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class KeyedLocks:
    def __init__(self):
        self._entries = {}

    def __len__(self):
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key):
        # No awaits between lookup and reference increment: a waiting task owns
        # its reference too, so a departing holder cannot replace its mutex.
        entry = self._entries.setdefault(key, _Entry())
        entry.users += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.users -= 1
            if entry.users == 0:
                del self._entries[key]
