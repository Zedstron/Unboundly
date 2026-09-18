from app.domain.models import AgentResponse
from app.infrastructure.ai import AIProvider
from app.infrastructure.mcp import registry as mcp_registry

from app.agents.state import PersonaGraphState


async def generate_response_node(state: PersonaGraphState, ai: AIProvider) -> dict[str, AgentResponse]:
    messages = state["messages"]

    if mcp_registry.has_tools:
        tools_schema = mcp_registry.get_tools_schema()
        result = await ai.chat_with_tools(messages, tools_schema, temperature=0.85)
    else:
        result = await ai.chat(messages, temperature=0.85)

    return { "agent_response": result }
