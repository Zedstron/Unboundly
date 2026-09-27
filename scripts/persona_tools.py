from __future__ import annotations


def filter_keys_for_persona(keys: list[str], persona_id: str) -> list[str]:
    prefixes = (
        f"persona:memory:short:{persona_id}",
        f"persona:memory:long:index:{persona_id}",
        f"persona:memory:long:{persona_id}",
    )
    return [key for key in keys if any(key.startswith(prefix) for prefix in prefixes)]


def format_conversation_rows(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        conversation_id = str(row.get("conversation_id", "<unknown>"))
        message_count = row.get("message_count", 0)
        last_message = str(row.get("last_message") or "").replace("\n", " ").strip()
        if not last_message:
            last_message = "n/a"
        if len(last_message) > 60:
            last_message = f"{last_message[:57]}..."
        lines.append(f"{conversation_id:<20}  messages={message_count:<3}  last={last_message}")
    return lines
