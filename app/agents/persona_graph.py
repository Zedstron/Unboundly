"""Multi-node LangGraph workflow for persona cognition and response generation."""

from collections.abc import Sequence
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logger import get_logger
from app.core.prompts import get_prompt
from app.domain.models import AgentResponse
from app.infrastructure.ai import AIProvider
from app.infrastructure.memory import LongTermMemory, ShortTermMemory
from app.infrastructure.sqlite import get_conversation

logger = get_logger(__name__)


class PersonaGraphState(TypedDict, total=False):
    """Ephemeral state shared by the nodes of one cognitive workflow run."""

    operation: Literal["classify", "reply"]
    conversation_id: str
    text: str
    initiative: str | None
    mood: dict[str, float]
    allowed_events: list[str]
    event: str
    history: list[dict[str, str]]
    short_memories: list[dict[str, Any]]
    long_memories: list[dict[str, Any]]
    messages: list[dict[str, str]]
    agent_response: AgentResponse
    reply: str


class PersonaAgentGraph:
    """Owns all model calls and their surrounding context/memory workflow.

    The graph has two entry paths:
    - classify_event -> END
    - retrieve_context -> build_prompt -> generate_response -> save_memories
      -> postprocess_response -> END

    ConversationService remains responsible for persistence, presence, and
    behavioural decisions; this class is the AI boundary.
    """

    def __init__(self, persona: dict[str, Any], redis: Redis) -> None:
        self.persona = persona
        self.ai = AIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            embedding_model=settings.embedding_model,
            embedding_base_url=settings.embedding_base_url,
            embedding_api_key=settings.embedding_api_key,
        )
        self.short_memory = ShortTermMemory(redis)
        self.long_memory = LongTermMemory(redis, self.ai)
        self.graph = self._build_graph()

    def update_persona(self, persona: dict[str, Any]) -> None:
        self.persona = persona

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
    ) -> str:
        result = await self.graph.ainvoke(
            {
                "operation": "reply",
                "conversation_id": conversation_id,
                "mood": mood,
                "initiative": initiative,
            }
        )
        return result["reply"]

    def _build_graph(self):
        builder = StateGraph(PersonaGraphState)
        builder.add_node("classify_event", self._classify_event_node)
        builder.add_node("retrieve_context", self._retrieve_context_node)
        builder.add_node("build_prompt", self._build_prompt_node)
        builder.add_node("generate_response", self._generate_response_node)
        builder.add_node("save_memories", self._save_memories_node)
        builder.add_node("postprocess_response", self._postprocess_response_node)

        builder.add_conditional_edges(
            START,
            self._route_entry,
            {"classify": "classify_event", "reply": "retrieve_context"},
        )
        builder.add_edge("classify_event", END)
        builder.add_edge("retrieve_context", "build_prompt")
        builder.add_edge("build_prompt", "generate_response")
        builder.add_edge("generate_response", "save_memories")
        builder.add_edge("save_memories", "postprocess_response")
        builder.add_edge("postprocess_response", END)
        return builder.compile()

    @staticmethod
    def _route_entry(state: PersonaGraphState) -> Literal["classify", "reply"]:
        return state["operation"]

    async def _classify_event_node(self, state: PersonaGraphState) -> dict[str, str]:
        text = state["text"]
        allowed_events = state["allowed_events"]
        if not text or not text.strip():
            return {"event": "long_idle_gap"}

        try:
            event = await self.ai.classify_event(text, allowed_events)
        except Exception as exc:
            logger.warning("Event classification failed; using local fallback: %s", exc)
            event = self._fallback_classify_event(text, allowed_events)
        return {"event": event}

    async def _retrieve_context_node(self, state: PersonaGraphState) -> dict[str, Any]:
        conversation_id = state["conversation_id"]
        memory_key = self.persona["id"] + conversation_id
        history = await get_conversation(self.persona["id"], conversation_id, limit=15)
        short_memories = await self.short_memory.get_short_memories(memory_key)
        long_memories: list[dict[str, Any]] = []
        if history:
            long_memories = await self.long_memory.search(memory_key, history[-1]["content"])

        return {
            "history": history,
            "short_memories": short_memories,
            "long_memories": long_memories,
        }

    def _build_prompt_node(self, state: PersonaGraphState) -> dict[str, list[dict[str, str]]]:
        system = get_prompt(
            "persona",
            {
                "name": self.persona.get("profile", {}).get("name", ""),
                "profile": self.persona.get("profile", {}),
                "traits": self.persona.get("traits", {}),
                "mood": state["mood"],
                "short_memories": state["short_memories"],
                "long_memories": state["long_memories"],
                "language_style": self.persona.get("profile", {}).get("language_style", []),
            },
        )
        if initiative := state.get("initiative"):
            system += (
                "\n\nINITIATIVE\nYou decided on your own to send a brief, natural "
                f"{initiative.replace('_', ' ')}. Do not imply that the other person "
                "just messaged. Return only the message you would send."
            )

        roles = {"bot": "assistant", "user": "user"}
        history = [
            {"role": roles[message["direction"]], "content": message["content"]}
            for message in state["history"]
        ]
        return {"messages": [{"role": "system", "content": system}, *history]}

    async def _generate_response_node(self, state: PersonaGraphState) -> dict[str, AgentResponse]:
        result = await self.ai.chat(state["messages"], temperature=0.85)
        return {"agent_response": result}

    async def _save_memories_node(self, state: PersonaGraphState) -> dict:
        memory_key = self.persona["id"] + state["conversation_id"]
        for memory in state["agent_response"].memories:
            payload = memory.model_dump()
            if memory.lifetime == "short":
                await self.short_memory.save_short_memory(memory_key, payload)
            elif memory.lifetime == "long":
                await self.long_memory.save(memory_key, payload)
        return {}

    @staticmethod
    def _postprocess_response_node(state: PersonaGraphState) -> dict[str, str]:
        reply = state["agent_response"].response.strip()
        if not reply:
            raise ValueError("Persona model returned an empty response")
        return {"reply": reply}

    @staticmethod
    def _fallback_classify_event(text: str, allowed_events: Sequence[str]) -> str:
        low = text.lower()
        candidates = (
            ("reassurance", ("sorry", "love you", "miss you", "reassure")),
            ("conflict", ("stupid", "hate you", "shut up", "angry")),
            ("compliment", ("beautiful", "cute", "good girl", "handsome")),
        )
        for event, keywords in candidates:
            if event in allowed_events and any(keyword in low for keyword in keywords):
                return event
        if "long_idle_gap" in allowed_events and len(low) < 3:
            return "long_idle_gap"
        return allowed_events[0]
