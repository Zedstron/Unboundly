import json
import time
import math
import uuid
from typing import Any, Protocol
from redis.asyncio import Redis

class Embeddings(Protocol):
    async def embed(self, text: str) -> list[float]: ...

class LongTermCache:
    def __init__(self, redis: Redis, embeddings: Embeddings) -> None:
        self.redis = redis
        self.embeddings = embeddings

    async def save(self, persona_id: str, memory: dict[str, Any]) -> None:
        embedding = await self.embeddings.embed(memory["content"])
        record = {**memory, "embedding": embedding}
        key = f"persona:memory:long:{persona_id}:{uuid.uuid4().hex}"

        await self.redis.set(key, json.dumps(record))
        await self.redis.sadd(f"persona:memory:long:index:{persona_id}", key)

    async def search(self, persona_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_vector = await self.embeddings.embed(query)
        keys = await self.redis.smembers(f"persona:memory:long:index:{persona_id}")

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

class ShortTermCache:
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

    async def save_short_memory(
        self,
        persona_id: str,
        memory: dict[str, Any],
        ttl: int = 4 * 60 * 60,
    ) -> None:
        key = f"persona:memory:short:{persona_id}"
        await self.redis.rpush(key, json.dumps(memory))
        await self.redis.ltrim(key, -1000, -1)
        await self.redis.expire(key, ttl)

    async def get_short_memories(self, persona_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.redis.lrange(f"persona:memory:short:{persona_id}", -limit, -1)
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
            f"persona:out:{persona_id}{conversation_id}",
            json.dumps({
                "type": "typing",
                "persona_id": persona_id,
                "conversation_id": conversation_id,
                "flag": flag,
            }),
        )

    async def schedule(self, key: str, payload: dict[str, Any], due_at: float) -> None:
        await self.redis.zadd("persona:schedule", {json.dumps({"key": key, "payload": payload}): due_at})

    async def due(self, now: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        now = now or time.time()
        rows = await self.redis.zrangebyscore("persona:schedule", 0, now, start=0, num=limit)

        if rows:
            await self.redis.zremrangebyscore("persona:schedule", 0, now)

        return [ json.loads(row) for row in rows ]
