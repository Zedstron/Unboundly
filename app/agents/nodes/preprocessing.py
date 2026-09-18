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

    system = get_prompt(
        "persona",
        {
            "name": persona.get("profile", {}).get("name", ""),
            "profile": persona.get("profile", {}),
            "traits": persona.get("traits", {}),
            "mood": state["mood"],
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

