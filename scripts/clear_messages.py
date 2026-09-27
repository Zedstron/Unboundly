from __future__ import annotations

import asyncio

from app.infrastructure.persona import PersonaStore
from app.infrastructure.sqlite import clear_persona_messages, get_persona_conversations, init_db
from scripts.persona_tools import format_conversation_rows


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
            print("Using the first persona by default.")
            return personas[0]
        if raw.isdigit() and 1 <= int(raw) <= len(personas):
            return personas[int(raw) - 1]
        print("Invalid selection. Please choose a number from the list.")


async def main() -> None:
    await init_db()

    while True:
        try:
            persona = await choose_persona()
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return

        persona_id = persona["id"]
        conversations = await get_persona_conversations(persona_id)

        print(f"\nPersona: {persona_id} ({persona.get('name', persona_id)})")
        if not conversations:
            print("No conversation history found for this persona.")
        else:
            print("Conversation IDs:")
            for line in format_conversation_rows(conversations):
                print(f"  {line}")

        print("\nOptions:")
        print("  [1] clear one conversation")
        print("  [2] clear all conversations")
        print("  [3] choose another persona")
        print("  [q] quit")

        choice = input("Choose an action: ").strip().lower()

        if choice in {"q", "quit", "exit"}:
            print("Exiting message cleaner.")
            return

        if choice == "1":
            if not conversations:
                print("There are no conversations to clear.")
                continue

            target = input("Enter the conversation ID to clear: ").strip()
            if not target:
                print("A conversation ID is required.")
                continue

            removed = await clear_persona_messages(persona_id, target)
            print(f"Removed {removed} message(s) from conversation '{target}'.")

        elif choice == "2":
            confirm = input(f"Clear ALL message history for persona '{persona_id}'? (y/N): ").strip().lower()
            if confirm in {"y", "yes"}:
                removed = await clear_persona_messages(persona_id)
                print(f"Removed {removed} message(s) for persona '{persona_id}'.")
            else:
                print("Cancelled.")

        elif choice == "3":
            continue

        else:
            print("Invalid option. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())
