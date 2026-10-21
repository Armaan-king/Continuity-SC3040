"""Small process-local caches. Persistent installations use PostgreSQL as truth."""

import asyncio
from collections import OrderedDict


class RecentCache(OrderedDict):
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > 128:
            self.popitem(last=False)


STREAMS = RecentCache()
BOMS = RecentCache()
FINDING_RECORDERS = RecentCache()
PROGRESS_TASKS: set[asyncio.Task] = set()
