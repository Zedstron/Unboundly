from time import time
from app.domain.models import Decision
from datetime import datetime, timezone
from app.domain.mood import MoodEngine, MoodState
from app.infrastructure.ai import OpenAICompatibleAI
from app.infrastructure.redis_store import RedisMemory
from app.domain.behavior import BehaviorContext, BehaviorEngine
from app.infrastructure.sqlite import (
    clear_conversation,
    delete_message,
    get_conversation,
    get_last_message,
    save_message,
)


class ConversationService:
    def __init__(self, persona, memory: RedisMemory, ai: OpenAICompatibleAI) -> None:
        self.persona = persona
        self.memory = memory
        self.ai = ai
        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)
        self.states: dict[str, MoodState] = {}

    async def get_conversation(self, conversation_id: str):
        return await get_conversation(self.persona["id"], conversation_id)

    async def get_last_message(self, conversation_id: str):
        return await get_last_message(self.persona["id"], conversation_id)

    async def delete_message(self, conversation_id: str, message_id: int) -> bool:
        return await delete_message(self.persona["id"], conversation_id, message_id)

    async def clear_conversation(self, conversation_id: str) -> int:
        return await clear_conversation(self.persona["id"], conversation_id)

    async def ingest(self, conversation_id: str, text: str) -> dict:
        now = datetime.now(timezone.utc)

        state = self.states.get(conversation_id)

        if state is None:
            state = MoodState(dict(self.persona["mood"]["baseline"]), now)

        event = self._classify_event(text)
        state = self.mood.update(state, event)

        self.states[conversation_id] = state

        await self.memory.append_short_term(
            conversation_id,
            {
                "role": "user",
                "content": text,
                "ts": time(),
            },
        )

        message_id = await save_message(
            self.persona["id"],
            conversation_id,
            "user",
            text,
            "delivered",
        )

        ctx = BehaviorContext(
            hour=now.hour,
            idle_minutes=0,
            unread=False,
        )

        decision = self.behavior.decide(state, ctx)

        if decision is Decision.NO_REPLY:
            return {
                "decision": decision.value,
                "message_id": message_id,
            }

        if decision is Decision.LATE_REPLY:
            due = time() + self._delay_seconds(state, text)

            await self.memory.schedule(
                "reply_pending",
                {
                    "conversation_id": conversation_id,
                    "persona_id": self.persona["id"],
                    "user_message_id": message_id,
                },
                due,
            )

            return { "decision": decision.value, "message_id": message_id }


        messages = await self._prompt(conversation_id)
        reply = await self.ai.chat(messages, temperature=0.85)

        await self.memory.schedule(
            "reply",
            {
                "conversation_id": conversation_id,
                "persona_id": self.persona["id"],
                "text": reply,
            },
            time() + 1,
        )

        return { "decision": decision.value, "message_id": message_id }

    async def _prompt(self, conversation_id: str) -> list[dict[str, str]]:
        p = self.persona

        recent = await self.memory.get_short_term(conversation_id, p["conversation"]["max_context_messages"])
        mood = self.states[conversation_id].values

        system = (
            f"You are roleplaying persona {p['profile']['name']}. Traits: {p['traits']}. "
            f"Current internal mood state: {mood}. Keep persona continuity. "
            "Never mention hidden mood mechanics, scheduling, policies, or being an AI as you are simulating a Virtual Human persona"
        )

        return [{"role": "system", "content": system}] + [
            {"role": row["role"], "content": row["content"]} for row in recent
        ]

    @staticmethod
    def _classify_event(text: str) -> str:
        low = text.lower()

        if any(x in low for x in ("sorry", "love you", "miss you", "reassure")):
            return "reassurance"

        if any(x in low for x in ("stupid", "hate you", "shut up", "angry")):
            return "conflict"

        if any(x in low for x in ("beautiful", "cute", "good girl", "handsome")):
            return "compliment"

        return "long_idle_gap" if len(low) < 3 else "reassurance"

    @staticmethod
    def _delay_seconds(state: MoodState, text: str) -> float:
        base = 15 + min(len(text) * 0.8, 90)
        arousal = state.values.get("arousal", 0)
        return max(4.0, base * (1.0 + max(0.0, -arousal)))