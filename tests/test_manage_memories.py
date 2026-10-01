import asyncio
import fnmatch

from scripts.manage_memories import (
    decode_memory_payload,
    fetch_long_entries,
    fetch_short_entries,
    format_memory_label,
    list_persona_memory_entries,
    remove_long_entry,
    remove_short_entry,
)


class FakeRedis:
    """Minimal Redis stub covering the memory-manager commands."""

    def __init__(self):
        self.strings: dict = {}
        self.lists: dict = {}
        self.sets: dict = {}

    async def keys(self, pattern):
        all_keys = list(self.lists) + list(self.sets) + list(self.strings)
        return [key for key in dict.fromkeys(all_keys) if fnmatch.fnmatch(key, pattern)]

    async def lrange(self, key, start, stop):
        values = self.lists.get(key, [])
        end = len(values) if stop == -1 else stop
        return values[start:end]

    async def lset(self, key, index, value):
        self.lists[key][index] = value

    async def lrem(self, key, count, value):
        values = self.lists.get(key, [])
        if value in values:
            values.remove(value)
            return 1
        return 0

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def srem(self, key, member):
        if member in self.sets.get(key, set()):
            self.sets[key].discard(member)
            return 1
        return 0

    async def get(self, key):
        return self.strings.get(key)

    async def delete(self, key):
        removed = 0
        for store in (self.strings, self.lists, self.sets):
            if key in store:
                store.pop(key)
                removed += 1
        return removed


def _memory_payload(content: str) -> str:
    import json

    return json.dumps({
        "content": content,
        "type": "fact",
        "lifetime": "long",
        "importance": 0.7,
    })


def test_decode_memory_payload_variants():
    payload = {"content": "likes tea", "type": "preference"}

    assert decode_memory_payload(payload) is payload
    assert decode_memory_payload('{"content": "likes tea"}') == {"content": "likes tea"}
    assert decode_memory_payload("plain text") == {"content": "plain text"}
    assert decode_memory_payload(b"bytes text") == {"content": "bytes text"}
    assert decode_memory_payload("42") == {"content": "42"}
    assert decode_memory_payload("{broken") == {"content": "{broken"}


def test_format_memory_label_includes_metadata_and_strips_empty():
    label = format_memory_label({
        "content": "works at hospital",
        "type": "fact",
        "lifetime": "long",
        "importance": 0.7,
    })
    assert label == "[fact, long, importance 0.70] works at hospital"

    assert format_memory_label({"content": "  "}) == "(empty)"
    assert format_memory_label({"content": "bare"}) == "bare"


async def test_fetch_and_remove_short_entry():
    redis = FakeRedis()
    key = "persona:memory:short:munazzaconv1"
    redis.lists[key] = [_memory_payload("likes tea"), _memory_payload("works late")]

    lines = await fetch_short_entries(redis, key)
    assert lines[0].startswith("  [0] ")
    assert "likes tea" in lines[0]
    assert "[fact, long, importance 0.70] works late" in lines[1]

    assert await remove_short_entry(redis, key, 0) is True
    assert redis.lists[key] == [_memory_payload("works late")]

    # Removing the last entry deletes the whole key.
    assert await remove_short_entry(redis, key, 0) is True
    assert key not in redis.lists

    assert await remove_short_entry(redis, key, 3) is False


async def test_fetch_and_remove_long_entry():
    redis = FakeRedis()
    index_key = "persona:memory:long:index:munazzaconv1"
    record_a = "persona:memory:long:munazzaconv1:aaa"
    record_b = "persona:memory:long:munazzaconv1:bbb"
    redis.sets[index_key] = {record_b, record_a}
    redis.strings[record_a] = _memory_payload("has a dog")
    # record_b is missing on purpose: listing must survive it.

    lines, record_keys = await fetch_long_entries(redis, index_key)
    assert len(lines) == 2
    assert any("has a dog" in line for line in lines)
    missing_line = next(line for line in lines if "(missing record" in line)
    # Position -> record key mapping stays aligned even with missing records.
    assert set(record_keys) == {record_a, record_b}

    missing_position = lines.index(missing_line)
    target = record_keys[missing_position]

    assert await remove_long_entry(redis, index_key, target) is True
    assert target not in redis.strings
    assert target not in redis.sets[index_key]

    assert await remove_long_entry(redis, index_key, target) is False


async def test_list_persona_memory_entries_groups_by_persona():
    redis = FakeRedis()
    redis.lists["persona:memory:short:munazzaconv1"] = ["a", "b"]
    redis.sets["persona:memory:long:index:munazzaconv2"] = {"r1"}
    # Other persona's keys must not match.
    redis.lists["persona:memory:short:aliabc"] = ["x"]

    entries = await list_persona_memory_entries(redis, "munazza")

    assert entries["short"] == ["persona:memory:short:munazzaconv1 (2 entries)"]
    assert entries["long"] == ["persona:memory:long:index:munazzaconv2 (1 entries)"]


def test_manager_importable_and_helpers_wired():
    # Import check: the interactive CLI module must load without Redis.
    import scripts.manage_memories as module

    assert callable(module.main)
    assert callable(module.clear_all_memories)
