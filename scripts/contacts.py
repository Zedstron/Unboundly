import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.sqlite import (
    init_db,
    list_contacts,
    get_contact,
    save_or_update_contact,
    update_contact_trust,
    delete_contact,
)
from app.infrastructure.persona import PersonaStore


def print_table(contacts: list[dict]):
    if not contacts:
        print("\n[!] No contacts found in database.\n")
        return

    print("\n" + "=" * 130)
    print(f"{'ID':<6} {'Persona':<12} {'Contact ID':<25} {'Name':<20} {'Trust':<10} {'Relationship':<14} {'Source':<10} {'Status':<8}")
    print("-" * 130)
    for c in contacts:
        cid = str(c.get("id", ""))
        pid = str(c.get("persona_id", ""))
        contact_id = str(c.get("contact_id", ""))
        name = str(c.get("name", "Unknown"))
        trust = f"{float(c.get('trust', 0.0)):+.3f}"
        relationship = str(c.get("relationship") or "-").replace("_", " ")
        source = str(c.get("source") or "local")
        status = "unknown" if c.get("is_unknown") else "known"
        print(f"{cid:<6} {pid:<12} {contact_id:<25} {name:<20} {trust:<10} {relationship:<14} {source:<10} {status:<8}")
    print("=" * 130 + "\n")


async def select_persona() -> str:
    store = PersonaStore()
    available = store.available_personas()
    if not available:
        return "munazza"
    if len(available) == 1:
        return available[0]["id"]

    print("Available personas:")
    for idx, p in enumerate(available, start=1):
        print(f"  [{idx}] {p['id']} ({p['name']})")
    
    choice = input("Select persona number (Enter for default): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(available):
        return available[int(choice) - 1]["id"]
    return available[0]["id"]


async def main():
    await init_db()

    while True:
        contacts = await list_contacts()
        print_table(contacts)

        print("Contacts Manager Menu:")
        print(" [1] Add / Save Contact")
        print(" [2] Update Contact Trust (Delta / Direct)")
        print(" [3] Remove Contact")
        print(" [4] Refresh List")
        print(" [5] Edit Contact (Name / Unknown Status)")
        print(" [q] Quit")

        choice = input("\nEnter option: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            print("Exiting contacts manager.")
            break

        elif choice == "1":
            persona_id = await select_persona()
            contact_id = input("Enter contact ID / phone / username: ").strip()
            if not contact_id:
                print("Contact ID cannot be empty.")
                continue
            name = input("Enter contact display name [Unknown]: ").strip() or "Unknown"
            trust_input = input("Enter initial trust (-1.0 to 1.0) [0.0]: ").strip()
            try:
                trust = float(trust_input) if trust_input else 0.0
            except ValueError:
                print("Invalid trust value; defaulting to 0.0")
                trust = 0.0
            relationship = input(
                "Relationship stage (blank = auto from trust) "
                "[stranger/acquaintance/friend/close_friend/best_friend/crush/flirting/"
                "in_love/boyfriend/girlfriend/partner/engaged/family/blocked]: "
            ).strip() or None
            source = input("Enter source platform (e.g. local, whatsapp, instagram) [local]: ").strip() or "local"
            status_input = input("Mark as unknown contact? (y/N): ").strip().lower()
            is_unknown = status_input in ("y", "yes")

            saved = await save_or_update_contact(
                persona_id=persona_id,
                contact_id=contact_id,
                name=name,
                trust=trust,
                source=source,
                is_unknown=is_unknown,
                relationship=relationship,
            )
            print(f"\n[+] Saved contact successfully: {saved}\n")

        elif choice == "2":
            persona_id = await select_persona()
            contact_id = input("Enter contact ID to update: ").strip()
            if not contact_id:
                print("Contact ID cannot be empty.")
                continue
            
            existing = await get_contact(persona_id, contact_id)
            if existing:
                print(f"Current contact: Name={existing['name']}, Trust={existing['trust']:+.3f}")
            else:
                print(f"Contact does not exist yet. Will create new entry with default trust 0.0.")

            mode = input("Update mode: [d]elta (e.g. +0.05 or -0.1), [s]et absolute trust, or [r]elationship stage? [d]: ").strip().lower()
            if mode == "s":
                val_str = input("Enter new absolute trust (-1.0 to 1.0): ").strip()
                try:
                    val = float(val_str)
                    name = input(f"Enter name [{existing['name'] if existing else 'Unknown'}]: ").strip() or (existing["name"] if existing else "Unknown")
                    updated = await save_or_update_contact(
                        persona_id=persona_id,
                        contact_id=contact_id,
                        name=name,
                        trust=val,
                        source=existing.get("source") if existing else "local",
                    )
                    print(f"\n[+] Updated contact trust: {updated}\n")
                except ValueError:
                    print("Invalid trust number.")
            elif mode == "r":
                current = existing.get("relationship") if existing else None
                stage = input(
                    f"Enter relationship stage [{current or 'auto'}] "
                    "(stranger/acquaintance/friend/close_friend/best_friend/crush/flirting/"
                    "in_love/boyfriend/girlfriend/partner/engaged/family/blocked, blank = auto): "
                ).strip() or None
                updated = await update_contact_trust(
                    persona_id=persona_id,
                    contact_id=contact_id,
                    delta=0.0,
                    relationship=stage,
                )
                print(f"\n[+] Updated relationship: {updated}\n")
            else:
                delta_str = input("Enter trust delta to add/subtract (e.g. 0.02 or -0.05): ").strip()
                try:
                    delta = float(delta_str)
                    updated = await update_contact_trust(
                        persona_id=persona_id,
                        contact_id=contact_id,
                        delta=delta,
                    )
                    print(f"\n[+] Updated contact trust: {updated}\n")
                except ValueError:
                    print("Invalid delta number.")

        elif choice == "3":
            persona_id = await select_persona()
            contact_id = input("Enter contact ID to delete: ").strip()
            if not contact_id:
                print("Contact ID cannot be empty.")
                continue
            confirm = input(f"Are you sure you want to delete contact '{contact_id}' for persona '{persona_id}'? (y/N): ").strip().lower()
            if confirm == "y":
                deleted = await delete_contact(persona_id, contact_id)
                if deleted:
                    print(f"\n[-] Contact '{contact_id}' deleted successfully.\n")
                else:
                    print(f"\n[!] Contact '{contact_id}' not found.\n")

        elif choice == "4":
            continue

        elif choice == "5":
            persona_id = await select_persona()
            contact_id = input("Enter contact ID to edit: ").strip()
            if not contact_id:
                print("Contact ID cannot be empty.")
                continue

            existing = await get_contact(persona_id, contact_id)
            if existing is None:
                print(f"\n[!] Contact '{contact_id}' not found.\n")
                continue

            current_status = "unknown" if existing.get("is_unknown") else "known"
            print(f"Current contact: Name={existing['name']}, Trust={existing['trust']:+.3f}, Status={current_status}")

            new_name = input(f"Enter new name [{existing['name']}]: ").strip() or existing["name"]
            status_input = input(
                f"Is this contact unknown? [y/N] (current: {current_status}): "
            ).strip().lower()
            if status_input in ("y", "yes"):
                is_unknown = True
            elif status_input in ("n", "no"):
                is_unknown = False
            else:
                is_unknown = bool(existing.get("is_unknown"))

            updated = await save_or_update_contact(
                persona_id=persona_id,
                contact_id=contact_id,
                name=new_name,
                trust=existing["trust"],
                source=existing.get("source"),
                is_unknown=is_unknown,
            )
            print(f"\n[+] Updated contact: {updated}\n")

        else:
            print("Invalid option. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())
