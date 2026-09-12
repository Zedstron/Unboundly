import asyncio
import json

from app.infrastructure.memory import ShortTermMemory


class FakeRedis:
    def __init__(self):
        self.scheduled = {}

    async def zadd(self, _key, values):
        self.scheduled.update(values)

    async def zrangebyscore(self, _key, minimum, maximum, start=0, num=None):
        rows = [row for row, score in self.scheduled.items() if minimum <= score <= maximum]
        rows.sort(key=self.scheduled.get)
        return rows[start : start + num]

    async def zrem(self, _key, row):
        return int(self.scheduled.pop(row, None) is not None)


async def _due_claims_only_the_returned_jobs():
    redis = FakeRedis()
    memory = ShortTermMemory(redis)

    await memory.schedule("reply", {"conversation_id": "a"}, 1)
    await memory.schedule("reply", {"conversation_id": "b"}, 1)
    await memory.schedule("reply", {"conversation_id": "c"}, 1)

    first = await memory.due(now=1, limit=2)
    remaining = await memory.due(now=1, limit=2)

    assert len(first) == 2
    assert len(remaining) == 1
    assert {item["payload"]["conversation_id"] for item in first + remaining} == {"a", "b", "c"}


def test_due_claims_only_the_returned_jobs():
    asyncio.run(_due_claims_only_the_returned_jobs())


async def _schedule_keeps_identical_payloads_as_distinct_jobs():
    redis = FakeRedis()
    memory = ShortTermMemory(redis)

    await memory.schedule("reply", {"conversation_id": "a"}, 1)
    await memory.schedule("reply", {"conversation_id": "a"}, 2)

    rows = [json.loads(row) for row in redis.scheduled]
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


def test_schedule_keeps_identical_payloads_as_distinct_jobs():
    asyncio.run(_schedule_keeps_identical_payloads_as_distinct_jobs())
