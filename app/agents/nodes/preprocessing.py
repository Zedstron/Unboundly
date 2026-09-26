from typing import Any
from app.core.prompts import get_prompt
from app.agents.state import PersonaGraphState
from app.infrastructure.mcp import registry as mcp_registry


def build_prompt_node(state: PersonaGraphState, persona: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    tools_block = ""
    if mcp_registry.has_tools:
        tool_summaries = [
            {
                "name": entry["function"]["name"],
                "description": entry["function"].get("description", ""),
            }
            for entry in mcp_registry.get_tools_schema()
        ]
        tools_block = get_prompt("tools_context", {"tools": tool_summaries})

    contact = state.get("contact")
    sender_name = state.get("sender_name")
    if contact is None:
        name_str = f" ({sender_name})" if sender_name and sender_name != "Unknown" else ""
        contact_context = f"Unknown contact / first message{name_str}. Internal Trust: 0.00 (Default: Unknown). You do not know this person yet."
    else:
        name_str = contact.get("name", "Unknown")
        trust_val = float(contact.get("trust", 0.0))
        contact_context = f"Saved contact: {name_str}. Internal Trust: {trust_val:+.3f} (Scale: -1.0 to 1.0)."

    system = get_prompt(
        "persona",
        {
            "name": persona.get("profile", {}).get("name", ""),
            "profile": persona.get("profile", {}),
            "traits": persona.get("traits", {}),
            "mood": state["mood"],
            "contact_context": contact_context,
            "short_memories": state["short_memories"],
            "long_memories": state["long_memories"],
            "language_style": persona.get("profile", {}).get("language_style", []),
            "tools_block": tools_block,
        },
    )

    if initiative := state.get("initiative"):
        system += (
            "\n\nINITIATIVE\nYou decided on your own to send a brief, natural "
            f"{initiative.replace('_', ' ')}. Do not imply that the other person "
            "just messaged. Return only the message you would send."
        )

    roles = { "bot": "assistant", "user": "user" }
    history = [
        { "role": roles[message["direction"]], "content": message["content"] }
        for message in state["history"]
    ]
    return { "messages": [{"role": "system", "content": system }, *history ]}

