from __future__ import annotations

import asyncio
import json
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.infrastructure.persona import PersonaStore


def decode_memory_payload(raw: Any) -> dict[str, Any]:
    """Decode one stored memory row into a dict.

    Current pipeline rows are JSON objects with content/type/lifetime/importance.
    Older or malformed rows fall back to a raw-content dict so they still display.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {"content": str(parsed)}
        except (json.JSONDecodeError, TypeError):
            return {"content": raw}

    return {"content": str(raw)}


def format_memory_label(payload: dict[str, Any]) -> str:
    """Human-readable one-line label for a memory payload (no embeddings)."""
    content = str(payload.get("content", "")).strip() or "(empty)"
    kind = payload.get("type")
    lifetime = payload.get("lifetime")
    importance = payload.get("importance")

    parts = []
    if kind:
        parts.append(str(kind))
    if lifetime:
        parts.append(str(lifetime))
    if importance is not None:
        try:
            parts.append(f"importance {float(importance):.2f}")
        except (TypeError, ValueError):
            pass

    label = f"[{', '.join(parts)}] " if parts else ""
    return f"{label}{content}"


async def fetch_short_entries(redis: Redis, key: str) -> list[str]:
    """Formatted short-memory entries for one list key, indexed by position."""
    rows = await redis.lrange(key, 0, -1)
    return [
        f"  [{position}] {format_memory_label(decode_memory_payload(row))}"
        for position, row in enumerate(rows)
    ]


async def fetch_long_entries(redis: Redis, index_key: str) -> tuple[list[str], list[Any]]:
    """Formatted long-memory entries for one index key.

    Returns the display lines and the underlying sorted record keys so a
    position chosen from the listing maps 1:1 to removal.
    """
    members = sorted(await redis.smembers(index_key))

    lines: list[str] = []
    record_keys: list[Any] = []
    for position, member in enumerate(members):
        raw = await redis.get(member)
        if raw is None:
            lines.append(f"  [{position}] (missing record: {member})")
        else:
            lines.append(f"  [{position}] {format_memory_label(decode_memory_payload(raw))}")
        record_keys.append(member)

    return lines, record_keys


async def list_persona_memory_entries(redis: Redis, persona_id: str) -> dict[str, list[str]]:
    """Key-level overview (counts only), aligned with the memory key layout:
    short: persona:memory:short:{persona_id}{conversation_id}
    long:  persona:memory:long:index:{persona_id}{conversation_id}
    """
    short_keys = [key.decode() if isinstance(key, bytes) else str(key)
                  for key in await redis.keys(f"persona:memory:short:{persona_id}*")]
    long_index_keys = [key.decode() if isinstance(key, bytes) else str(key)
                       for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")]

    short_entries = []
    for key in sorted(short_keys):
        rows = await redis.lrange(key, 0, -1)
        short_entries.append(f"{key} ({len(rows)} entries)")

    long_entries = []
    for index_key in sorted(long_index_keys):
        members = await redis.smembers(index_key)
        long_entries.append(f"{index_key} ({len(members)} entries)")

    return {"short": short_entries, "long": long_entries}


async def remove_short_entry(redis: Redis, key: str, position: int) -> bool:
    """Remove one entry from a short-memory list, preserving the order of the rest."""
    rows = await redis.lrange(key, 0, -1)
    if position < 0 or position >= len(rows):
        return False

    await redis.lset(key, position, "__deleted__")
    await redis.lrem(key, 1, "__deleted__")

    remaining = await redis.lrange(key, 0, -1)
    if not remaining:
        await redis.delete(key)

    return True


async def remove_long_entry(redis: Redis, index_key: str, record_key: Any) -> bool:
    """Remove one long-memory record and drop it from its index."""
    await redis.delete(record_key)
    removed = await redis.srem(index_key, record_key)
    return bool(removed)


async def delete_short_memory(redis: Redis, key: str) -> bool:
    deleted = await redis.delete(key)
    return bool(deleted)


async def delete_long_memory(redis: Redis, index_key: str) -> int:
    members = await redis.smembers(index_key)
    count = 0
    for member in members:
        await redis.delete(member)
        await redis.srem(index_key, member)
        count += 1
    await redis.delete(index_key)
    return count


async def clear_all_memories(redis: Redis, persona_id: str) -> dict[str, int]:
    short_keys = [key.decode() if isinstance(key, bytes) else str(key)
                  for key in await redis.keys(f"persona:memory:short:{persona_id}*")]
    long_index_keys = [key.decode() if isinstance(key, bytes) else str(key)
                       for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")]

    short_removed = 0
    for key in short_keys:
        short_removed += int(await redis.delete(key) or 0)

    long_removed = 0
    for index_key in long_index_keys:
        members = await redis.smembers(index_key)
        for member in members:
            await redis.delete(member)
            await redis.srem(index_key, member)
            long_removed += 1
        await redis.delete(index_key)

    return {"short": short_removed, "long": long_removed}


async def choose_persona() -> dict:
    personas = PersonaStore().available_personas()
    if not personas:
        raise RuntimeError("No persona definitions were found in the personas folder.")

    if len(personas) == 1:
        return personas[0]

    print("Available personas:")
    for index, persona in enumerate(personas, start=1):
        print(f"  [{index}] {persona['id']} ({persona.get('name', persona['id'])})")

    while True:
        raw = input("Select persona number: ").strip()
        if not raw:
            return personas[0]
        if raw.isdigit() and 1 <= int(raw) <= len(personas):
            return personas[int(raw) - 1]
        print("Invalid selection. Please choose a number from the list.")


async def _show_details(redis: Redis, persona_id: str) -> None:
    """Print full memory contents per key, as stored by the memory agent."""
    short_keys = sorted(
        key.decode() if isinstance(key, bytes) else str(key)
        for key in await redis.keys(f"persona:memory:short:{persona_id}*")
    )
    long_index_keys = sorted(
        key.decode() if isinstance(key, bytes) else str(key)
        for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")
    )

    print("\nShort memories (contents):")
    if not short_keys:
        print("  (none)")
    for key in short_keys:
        print(f" {key}")
        lines = await fetch_short_entries(redis, key)
        print("\n".join(lines) if lines else "  (empty)")

    print("\nLong memories (contents):")
    if not long_index_keys:
        print("  (none)")
    for index_key in long_index_keys:
        print(f" {index_key}")
        lines, _ = await fetch_long_entries(redis, index_key)
        print("\n".join(lines) if lines else "  (empty)")


async def _pick_list_key(keys: list[str], label: str) -> str | None:
    if not keys:
        print(f"There are no {label} keys.")
        return None

    for index, key in enumerate(keys, start=1):
        print(f"  [{index}] {key}")

    raw = input("Select key number (blank to cancel): ").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(keys):
        return keys[int(raw) - 1]

    print("Invalid selection.")
    return None


async def _remove_short_flow(redis: Redis, persona_id: str) -> None:
    keys = sorted(
        key.decode() if isinstance(key, bytes) else str(key)
        for key in await redis.keys(f"persona:memory:short:{persona_id}*")
    )
    key = await _pick_list_key(keys, "short-memory")
    if key is None:
        return

    lines = await fetch_short_entries(redis, key)
    if not lines:
        print("That key has no entries.")
        return

    print("\n".join(lines))
    raw = input("Entry number to remove (blank to cancel): ").strip()
    if not raw or not raw.isdigit():
        return

    if await remove_short_entry(redis, key, int(raw)):
        print("Entry removed.")
    else:
        print("Invalid entry number.")


async def _remove_long_flow(redis: Redis, persona_id: str) -> None:
    keys = sorted(
        key.decode() if isinstance(key, bytes) else str(key)
        for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")
    )
    index_key = await _pick_list_key(keys, "long-memory")
    if index_key is None:
        return

    lines, record_keys = await fetch_long_entries(redis, index_key)
    if not lines:
        print("That index has no entries.")
        return

    print("\n".join(lines))
    raw = input("Entry number to remove (blank to cancel): ").strip()
    if not raw or not raw.isdigit() or int(raw) >= len(record_keys):
        print("Invalid entry number.")
        return

    if await remove_long_entry(redis, index_key, record_keys[int(raw)]):
        print("Entry removed.")
    else:
        print("Entry could not be removed.")


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    try:
        await redis.ping()
    except Exception:
        print(f"[!] Redis is not reachable at {settings.redis_url}. Start Redis before running this manager.")
        return

    while True:
        try:
            persona = await choose_persona()
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return

        persona_id = persona["id"]
        entries = await list_persona_memory_entries(redis, persona_id)

        print(f"\nPersona: {persona_id} ({persona.get('name', persona_id)})")
        print("Short memories:")
        if entries["short"]:
            for key in entries["short"]:
                print(f"  - {key}")
        else:
            print("  (none)")

        print("Long memories:")
        if entries["long"]:
            for key in entries["long"]:
                print(f"  - {key}")
        else:
            print("  (none)")

        print("\nOptions:")
        print("  [1] show memory contents (list entries)")
        print("  [2] remove one short-memory entry")
        print("  [3] remove one long-memory entry")
        print("  [4] delete one short-memory key")
        print("  [5] delete one long-memory index")
        print("  [6] clear all short memories")
        print("  [7] clear all long memories")
        print("  [8] clear all memories")
        print("  [9] choose another persona")
        print("  [q] quit")

        choice = input("Choose an action: ").strip().lower()

        if choice in {"q", "quit", "exit"}:
            print("Exiting memory manager.")
            return

        if choice == "1":
            await _show_details(redis, persona_id)

        elif choice == "2":
            await _remove_short_flow(redis, persona_id)

        elif choice == "3":
            await _remove_long_flow(redis, persona_id)

        elif choice == "4":
            keys = sorted(
                key.decode() if isinstance(key, bytes) else str(key)
                for key in await redis.keys(f"persona:memory:short:{persona_id}*")
            )
            key = await _pick_list_key(keys, "short-memory")
            if key:
                deleted = await delete_short_memory(redis, key)
                print(f"Deleted short-memory key '{key}': {deleted}")

        elif choice == "5":
            keys = sorted(
                key.decode() if isinstance(key, bytes) else str(key)
                for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")
            )
            key = await _pick_list_key(keys, "long-memory")
            if key:
                removed = await delete_long_memory(redis, key)
                print(f"Deleted {removed} long-memory entries for '{key}'.")

        elif choice == "6":
            confirm = input(f"Delete ALL short memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                keys = [key.decode() if isinstance(key, bytes) else str(key)
                        for key in await redis.keys(f"persona:memory:short:{persona_id}*")]
                removed = sum(int(await redis.delete(key) or 0) for key in keys)
                print(f"Deleted {removed} short-memory entries.")
            else:
                print("Cancelled.")

        elif choice == "7":
            confirm = input(f"Delete ALL long memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                keys = [key.decode() if isinstance(key, bytes) else str(key)
                        for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")]
                removed = 0
                for index_key in keys:
                    removed += await delete_long_memory(redis, index_key)
                print(f"Deleted {removed} long-memory entries.")
            else:
                print("Cancelled.")

        elif choice == "8":
            confirm = input(f"Delete ALL memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                removed = await clear_all_memories(redis, persona_id)
                print(f"Deleted {removed['short']} short and {removed['long']} long memory entries.")
            else:
                print("Cancelled.")

        elif choice == "9":
            continue

        else:
            print("Invalid option. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())
