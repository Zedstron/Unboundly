from __future__ import annotations

import asyncio
from redis.asyncio import Redis

from app.core.config import settings
from app.infrastructure.persona import PersonaStore


async def list_persona_memory_entries(redis: Redis, persona_id: str) -> dict[str, list[str]]:
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
        print("  [1] delete one short-memory key")
        print("  [2] delete one long-memory index")
        print("  [3] clear all short memories")
        print("  [4] clear all long memories")
        print("  [5] clear all memories")
        print("  [6] choose another persona")
        print("  [q] quit")

        choice = input("Choose an action: ").strip().lower()

        if choice in {"q", "quit", "exit"}:
            print("Exiting memory manager.")
            return

        if choice == "1":
            if not entries["short"]:
                print("There are no short memories to delete.")
                continue
            key = input("Enter the short-memory key to delete: ").strip()
            if not key:
                print("A key is required.")
                continue
            deleted = await delete_short_memory(redis, key)
            print(f"Deleted short-memory key '{key}': {deleted}")

        elif choice == "2":
            if not entries["long"]:
                print("There are no long-memory indexes to delete.")
                continue
            key = input("Enter the long-memory index key to delete: ").strip()
            if not key:
                print("A key is required.")
                continue
            removed = await delete_long_memory(redis, key)
            print(f"Deleted {removed} long-memory entries for '{key}'.")

        elif choice == "3":
            confirm = input(f"Delete ALL short memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                short_keys = [key.decode() if isinstance(key, bytes) else str(key)
                              for key in await redis.keys(f"persona:memory:short:{persona_id}*")]
                removed = sum(int(await redis.delete(key) or 0) for key in short_keys)
                print(f"Deleted {removed} short-memory entries.")
            else:
                print("Cancelled.")

        elif choice == "4":
            confirm = input(f"Delete ALL long memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                long_index_keys = [key.decode() if isinstance(key, bytes) else str(key)
                                   for key in await redis.keys(f"persona:memory:long:index:{persona_id}*")]
                removed = 0
                for index_key in long_index_keys:
                    members = await redis.smembers(index_key)
                    for member in members:
                        await redis.delete(member)
                        await redis.srem(index_key, member)
                        removed += 1
                    await redis.delete(index_key)
                print(f"Deleted {removed} long-memory entries.")
            else:
                print("Cancelled.")

        elif choice == "5":
            confirm = input(f"Delete ALL memories for '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                removed = await clear_all_memories(redis, persona_id)
                print(f"Deleted {removed['short']} short and {removed['long']} long memory entries.")
            else:
                print("Cancelled.")

        elif choice == "6":
            continue

        else:
            print("Invalid option. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())
