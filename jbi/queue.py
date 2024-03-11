import itertools
import json
import logging
import os
import shutil
import traceback
from abc import ABC
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from jbi import bugzilla
from jbi.environment import get_settings

logger = logging.getLogger(__name__)


class PythonException(BaseModel, frozen=True):
    type: str
    description: str
    details: str

    @classmethod
    def from_exc(cls, exc: Exception):
        return PythonException(
            type=exc.__class__.__name__,
            description=str(exc),
            details="".join(traceback.format_exception(exc)),
        )


class QueueItem(BaseModel, frozen=True):
    """Dead Letter Queue entry."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    payload: bugzilla.WebhookRequest
    error: Optional[PythonException] = None

    @property
    def identifier(self):
        return f"{self.timestamp.timestamp():.0f}-{self.payload.event.action}-{"error" if self.error else "postponed"}"


@lru_cache(maxsize=1)
def get_dl_queue():
    settings = get_settings()
    return DeadLetterQueue(settings.dl_queue_dsn)


class QueueBackend(ABC):
    """An interface for dead letter queues."""

    async def clear(self):
        pass

    async def put(self, item: QueueItem):
        pass

    async def remove(self, bug_id: int, identifier: str):
        pass

    async def get(self, bug_id: int) -> list[QueueItem]:
        return []

    async def get_all(self) -> dict[int, list[QueueItem]]:
        return {}

    async def size(self) -> int:
        return 0


class MemoryBackend(QueueBackend):
    def __init__(self):
        self.existing: dict[int, list[QueueItem]] = defaultdict(list)

    async def clear(self):
        self.existing.clear()

    async def put(self, item: QueueItem):
        self.existing[item.payload.bug.id].append(item)

    async def get(self, bug_id: int) -> list[QueueItem]:
        return self.existing[bug_id]

    async def get_all(self) -> dict[int, list[QueueItem]]:
        return self.existing

    async def remove(self, bug_id: int, identifier: str):
        self.existing[bug_id] = [
            i for i in self.existing[bug_id] if i.identifier != identifier
        ]

    async def size(self) -> int:
        return sum(len(v) for v in self.existing.values())


class FileBackend(QueueBackend):
    def __init__(self, location):
        if not os.path.exists(location):
            os.makedirs(location)
        elif not os.path.isdir(location):
            raise ValueError(f"{location} is not a directory")
        self.location = Path(location)

    async def clear(self):
        shutil.rmtree(self.location)

    async def put(self, item: QueueItem):
        folder = self.location / f"{item.payload.bug.id}"
        path = folder / (item.identifier + ".json")
        os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            f.write(item.model_dump_json())
        logger.info("%d items in dead letter queue", await self.size())

    async def remove(self, bug_id: int, identifier: str):
        path = self.location / f"{bug_id}" / (identifier + ".json")
        os.remove(path)

    async def get(self, bug_id: int) -> list[QueueItem]:
        folder = self.location / f"{bug_id}"
        results = []
        for filepath in folder.glob("*.json"):
            with open(folder / filepath) as f:
                results.append(QueueItem(**json.load(f)))
        return results

    async def get_all(self) -> dict[int, list[QueueItem]]:
        all_items = []
        for filepath in self.location.rglob("*.json"):
            with open(self.location / filepath) as f:
                all_items.append(QueueItem(**json.load(f)))
        by_bug = itertools.groupby(all_items, key=lambda i: i.payload.bug.id)
        return {k: list(v) for k, v in by_bug}

    async def size(self) -> int:
        return sum(1 for _ in self.location.rglob("*.json"))


class InvalidQueueDSNError(Exception):
    pass


class DeadLetterQueue:
    backend: QueueBackend

    def __init__(self, dsn: str):
        parsed = urlparse(url=dsn)
        if parsed.scheme == "memory":
            self.backend = MemoryBackend()
        elif parsed.scheme == "file":
            self.backend = FileBackend(parsed.path)
        else:
            raise InvalidQueueDSNError(f"{parsed.scheme} is not supported")

    async def postpone(self, payload: bugzilla.WebhookRequest):
        """
        Store the specified payload and exception information into the queue.
        """
        item = QueueItem(payload=payload)
        await self.backend.put(item)

    async def track_failed(self, payload: bugzilla.WebhookRequest, exc: Exception):
        """
        Store the specified payload and exception information into the queue.
        """
        item = QueueItem(
            payload=payload,
            error=PythonException.from_exc(exc),
        )
        await self.backend.put(item)

    async def is_blocked(self, payload: bugzilla.WebhookRequest) -> bool:
        """
        Return `True` if the specified `payload` is blocked and should be
        queued instead of being processed.
        """
        existing = await self.backend.get(payload.bug.id)
        return len(existing) > 0

    async def retrieve(self) -> list[QueueItem]:
        """
        Returns the whole list of items in the queue, grouped by bug, and
        sorted from oldest to newest.
        """
        existing = await self.backend.get_all()
        # Sort by event datetime ascending.
        sorted_existing = [
            sorted(v, key=lambda i: i.payload.event.time or 0)
            for v in existing.values()
            if len(v) > 0
        ]
        by_oldest_bug_events = sorted(
            sorted_existing, key=lambda items: items[0].payload.event.time or 0
        )
        return list(itertools.chain(*by_oldest_bug_events))

    async def done(self, item: QueueItem):
        """
        Mark item as done, remove from queue.
        """
        return await self.backend.remove(item.payload.bug.id, item.identifier)
