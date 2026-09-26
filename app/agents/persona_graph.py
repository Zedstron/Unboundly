from collections.abc import Sequence
from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from redis.asyncio import Redis

from app.core.config import settings
from app.infrastructure.ai import AIProvider
from app.infrastructure.memory import LongTermMemory, ShortTermMemory

from .nodes import (
    build_prompt_node,
    classify_event_node,
    generate_response_node,
    postprocess_response_node,
    retrieve_context_node,
    save_memories_node,
)
from .state import PersonaGraphState


class PersonaAgentGraph:
    def __init__(self, persona: dict[str, Any], redis: Redis) -> None:
        self.persona = persona
        self.ai = AIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            embedding_model=settings.embedding_model,
            embedding_base_url=settings.embedding_base_url,
            embedding_api_key=settings.embedding_api_key,
            local_embedding_generator=settings.local_embedding_generator,
            local_embedding_model=settings.local_embedding_model,
        )
        self.short_memory = ShortTermMemory(redis)
        self.long_memory = LongTermMemory(redis, self.ai)
        self.graph = self._build_graph()

    def update_persona(self, persona: dict[str, Any]) -> None:
        self.persona = persona
        self.graph = self._build_graph()

    def rebuild_graph(self) -> None:
        self.graph = self._build_graph()



    async def classify_event(self, text: str, allowed_events: Sequence[str]) -> str:
        result = await self.graph.ainvoke(
            {
                "operation": "classify",
                "text": text,
                "allowed_events": list(allowed_events),
            }
        )
        return result["event"]

    async def generate_reply(
        self,
        conversation_id: str,
        mood: dict[str, float],
        initiative: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        source: str | None = None,
        text: str | None = None,
    ) -> str:
        result = await self.graph.ainvoke(
            {
                "operation": "reply",
                "conversation_id": conversation_id,
                "mood": mood,
                "initiative": initiative,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "source": source,
                "text": text,
            }
        )
        return result["reply"]



    def _build_graph(self):
        builder = StateGraph(PersonaGraphState)

        persona_id = self.persona["id"]

        builder.add_node(
            "classify_event",
            partial(classify_event_node, ai=self.ai),
        )
        builder.add_node(
            "retrieve_context",
            partial(
                retrieve_context_node,
                persona_id=persona_id,
                short_memory=self.short_memory,
                long_memory=self.long_memory,
            ),
        )
        builder.add_node(
            "build_prompt",
            partial(build_prompt_node, persona=self.persona),
        )
        builder.add_node(
            "generate_response",
            partial(generate_response_node, ai=self.ai),
        )
        builder.add_node(
            "save_memories",
            partial(
                save_memories_node,
                persona_id=persona_id,
                short_memory=self.short_memory,
                long_memory=self.long_memory,
            ),
        )
        builder.add_node("postprocess_response", postprocess_response_node)

        builder.add_conditional_edges(
            START,
            _route_entry,
            { "classify": "classify_event", "reply": "retrieve_context" }
        )

        builder.add_edge("classify_event", END)

        builder.add_edge("retrieve_context", "build_prompt")
        builder.add_edge("build_prompt", "generate_response")
        builder.add_edge("generate_response", "save_memories")
        builder.add_edge("save_memories", "postprocess_response")
        builder.add_edge("postprocess_response", END)

        return builder.compile()


def _route_entry(state: PersonaGraphState) -> Literal["classify", "reply"]:
    return state["operation"]