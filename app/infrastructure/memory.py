import json
import time
import math
import uuid
from typing import Any, Protocol
from redis.asyncio import Redis

class Embeddings(Protocol):
    async def embed(self, text: str) -> list[float]: ...

class LongTermMemory:
    def __init__(self, redis: Redis, embeddings: Embeddings) -> None:
        self.redis = redis
        self.embeddings = embeddings

    async def save(self, key: str, memory: dict[str, Any]) -> None:
        embedding = await self.embeddings.embed(memory["content"])
        record = {**memory, "embedding": embedding}
        index_key = f"persona:memory:long:index:{key}"
        record_key = f"persona:memory:long:{key}:{uuid.uuid4().hex}"

        await self.redis.set(record_key, json.dumps(record))
        await self.redis.sadd(index_key, record_key)

    async def search(self, key: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_vector = await self.embeddings.embed(query)
        keys = await self.redis.smembers(f"persona:memory:long:index:{key}")

        records = []
        for key in keys:
            raw = await self.redis.get(key)
            if not raw:
                continue

            record = json.loads(raw)
            vector = record.pop("embedding", [])
            score = self._cosine(query_vector, vector)
            records.append((score, record))

        return [record for _, record in sorted(records, reverse=True, key=lambda item: item[0])[:limit]]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0

        denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
            sum(value * value for value in right)
        )

        return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0

class ShortTermMemory:
    def __init__(self, client: Redis) -> None:
        self.redis = client

    async def save_short_memory(
        self,
        key: str,
        memory: dict[str, Any],
        ttl: int = 4 * 60 * 60,
    ) -> None:
        key = f"persona:memory:short:{key}"
        await self.redis.rpush(key, json.dumps(memory))
        await self.redis.ltrim(key, -1000, -1)
        await self.redis.expire(key, ttl)

    async def get_short_memories(self, key: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.redis.lrange(f"persona:memory:short:{key}", -limit, -1)
        return [json.loads(row) for row in rows]

    async def set_json(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        await self.redis.set(key, json.dumps(value))
        if ttl is not None:
            await self.redis.expire(key, ttl)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        value = await self.redis.get(key)
        return json.loads(value) if value else None

    async def get_persona_state(self, persona_id: str) -> dict[str, Any] | None:
        return await self.get_json(f"persona:state:{persona_id}")

    async def set_persona_state(self, persona_id: str, state: dict[str, Any]) -> None:
        await self.set_json(f"persona:state:{persona_id}", state)

    async def get_presence(self, persona_id: str) -> dict[str, Any] | None:
        return await self.get_json(f"persona:presence:{persona_id}")

    async def set_presence(self, persona_id: str, presence: dict[str, Any]) -> None:
        await self.set_json(f"persona:presence:{persona_id}", presence)

    async def publish_typing(self, persona_id: str, conversation_id: str, flag: bool) -> None:
        await self.redis.publish(
            f"persona:out:{persona_id}:{conversation_id}",
            json.dumps({
                "type": "typing",
                "persona_id": persona_id,
                "conversation_id": conversation_id,
                "flag": flag,
            }),
        )

    async def schedule(self, key: str, payload: dict[str, Any], due_at: float) -> None:
        item = json.dumps({"id": uuid.uuid4().hex, "key": key, "payload": payload})
        await self.redis.zadd("persona:schedule", {item: due_at})

    async def due(self, now: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        now = now or time.time()
        rows = await self.redis.zrangebyscore("persona:schedule", 0, now, start=0, num=limit)

        due_items = []
        for row in rows:
            # Claim each exact item.  The old zremrangebyscore call removed
            # every due task, including tasks beyond ``limit`` and tasks
            # added by another worker between the read and delete.
            if await self.redis.zrem("persona:schedule", row):
                due_items.append(json.loads(row))

        return due_items

    async def claim_follow_up(self, persona_id: str, conversation_id: str, ttl: int) -> bool:
        key = f"persona:follow-up:pending:{persona_id}:{conversation_id}"
        return bool(await self.redis.set(key, "1", ex=ttl, nx=True))

    async def reserve_daily_follow_up(self, key: str, budget: int) -> bool:
        """Reserve one daily initiation slot before queuing its task."""
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 2 * 24 * 60 * 60)
        if count <= budget:
            return True
        await self.redis.decr(key)
        return False

    async def release_follow_up_claim(self, persona_id: str, conversation_id: str) -> None:
        await self.redis.delete(f"persona:follow-up:pending:{persona_id}:{conversation_id}")
