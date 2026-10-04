from typing import Any
from app.core.prompts import get_prompt
from app.agents.state import PersonaGraphState
from app.domain.relationship import describe_relationship
from app.infrastructure.mcp import registry as mcp_registry


def _reply_context(state: PersonaGraphState) -> str:
    """Describe an inbound reply so the agent can quote it back when useful."""
    reply_to_text = (state.get("reply_to_text") or "").strip()
    if not reply_to_text:
        return "(none — this is a new message, not a reply)"

    return (
        "The latest message is a reply to an earlier message. "
        f'The quoted text was: "{reply_to_text}". '
        "If you answer with a quoted reply, quote that message."
    )


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
    persona_gender = (persona.get("profile", {}) or {}).get("gender")

    if contact is None:
        name_str = f" ({sender_name})" if sender_name and sender_name != "Unknown" else ""
        contact_context = (
            f"Unknown contact / first message{name_str}. "
            + describe_relationship(
                0.0,
                sender_name,
                persona_gender=persona_gender,
                is_unknown=True,
            )
        )
    else:
        contact_context = describe_relationship(
            float(contact.get("trust", 0.0)),
            contact.get("name"),
            persona_gender=persona_gender,
            is_unknown=bool(contact.get("is_unknown")),
        )

    system = get_prompt(
        "persona",
        {
            "name": persona.get("profile", {}).get("name", ""),
            "profile": persona.get("profile", {}),
            "traits": persona.get("traits", {}),
            "mood": state["mood"],
            "contact_context": contact_context,
            "reply_context": _reply_context(state),
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

    if confide_context := state.get("confide_context"):
        system += "\n\nCONFIDING IN THIS PERSON\n" + confide_context

    roles = { "bot": "assistant", "user": "user" }
    history = [
        { "role": roles[message["direction"]], "content": message["content"] }
        for message in state["history"]
    ]
    return { "messages": [{"role": "system", "content": system }, *history ]}
