import json
import time
from typing import Any
from redis.asyncio import Redis


class RedisMemory:
    def __init__(self, client: Redis) -> None:
        self.redis = client

    async def append_short_term(self, conversation_id: str, message: dict[str, Any], ttl: int = 86400) -> None:
        key = f"persona:stm:{conversation_id}"
        await self.redis.rpush(key, json.dumps(message))
        await self.redis.ltrim(key, -100, -1)
        await self.redis.expire(key, ttl)

    async def get_short_term(self, conversation_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self.redis.lrange(f"persona:stm:{conversation_id}", -limit, -1)
        return [json.loads(row) for row in rows]

    async def schedule(self, key: str, payload: dict[str, Any], due_at: float) -> None:
        await self.redis.zadd("persona:schedule", {json.dumps({"key": key, "payload": payload}): due_at})

    async def due(self, now: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        now = now or time.time()
        rows = await self.redis.zrangebyscore("persona:schedule", 0, now, start=0, num=limit)

        if rows:
            await self.redis.zremrangebyscore("persona:schedule", 0, now)

        return [ json.loads(row) for row in rows ]
