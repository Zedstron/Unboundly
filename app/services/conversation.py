import random
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
    get_unread_user_messages,
    get_conversation,
    get_last_message,
    mark_message_seen_for_persona,
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

        state = await self._load_state()

        event = self._classify_event(text)
        state = self.mood.update(state, event)

        self.states[conversation_id] = state
        await self._save_state(state)

        presence = await self.memory.get_presence(self.persona["id"])

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
            online=bool(presence and presence.get("online")),
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
        state = self.states.get(conversation_id) or await self._load_state()
        self.states[conversation_id] = state
        mood = state.values

        system = (
            f"You are roleplaying persona {p['profile']['name']}. Traits: {p['traits']}. "
            f"Current internal mood state: {mood}. Keep persona continuity. "
            "Never mention hidden mood mechanics, scheduling, policies, or being an AI as you are simulating a Virtual Human persona"
        )

        return [{"role": "system", "content": system}] + [
            {"role": row["role"], "content": row["content"]} for row in recent
        ]

    async def _load_state(self) -> MoodState:
        stored = await self.memory.get_persona_state(self.persona["id"])
        if stored:
            return MoodState(
                values={key: float(value) for key, value in stored["mood"].items()},
                updated_at=datetime.fromisoformat(stored["updated_at"]),
            )
        return MoodState(dict(self.persona["mood"]["baseline"]), datetime.now(timezone.utc))

    async def _save_state(self, state: MoodState) -> None:
        await self.memory.set_persona_state(
            self.persona["id"],
            {"mood": state.values, "updated_at": state.updated_at.isoformat()},
        )

    async def life_tick(self) -> dict:
        state = await self._load_state()
        state = self.mood.update(state, "time_passed")
        await self._save_state(state)

        now = datetime.now(timezone.utc)
        probability = self.behavior.online_probability(now.hour, state)
        previous = await self.memory.get_presence(self.persona["id"])
        online = random.random() < probability
        if previous and previous.get("online") and random.random() < 0.75:
            online = True

        changed = not previous or previous.get("online") != online
        presence = {
            "online": online,
            "last_seen": now.timestamp() if online else (previous or {}).get("last_seen", now.timestamp()),
            "probability": probability,
            "updated_at": now.timestamp(),
        }
        await self.memory.set_presence(self.persona["id"], presence)
        return {
            "presence": presence,
            "changed": changed,
            "became_online": online and not (previous or {}).get("online", False),
        }

    async def get_presence(self) -> dict:
        presence = await self.memory.get_presence(self.persona["id"])
        if presence:
            return presence
        result = await self.life_tick()
        return result["presence"]

    async def process_unread_messages(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        actions = []
        state = await self._load_state()

        for message in await get_unread_user_messages(self.persona["id"]):
            message_id = await mark_message_seen_for_persona(
                self.persona["id"], message["conversation_id"], message["id"]
            )
            if not message_id:
                continue

            state = self.mood.update(state, self._classify_event(message["content"]))
            self.states[message["conversation_id"]] = state

            created_at = message["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            idle_minutes = max(0.0, (now - created_at).total_seconds() / 60)
            decision = self.behavior.decide(
                state,
                BehaviorContext(
                    hour=now.hour,
                    idle_minutes=idle_minutes,
                    unread=True,
                    online=True,
                ),
            )
            actions.append({
                "message_id": message_id,
                "conversation_id": message["conversation_id"],
                "decision": decision.value,
                "content": message["content"],
            })

        await self._save_state(state)

        for action in actions:
            if action["decision"] == Decision.LATE_REPLY.value:
                await self.memory.schedule(
                    "reply",
                    {
                        "conversation_id": action["conversation_id"],
                        "persona_id": self.persona["id"],
                    },
                    time() + self._delay_seconds(state, action["content"]),
                )
            elif action["decision"] == Decision.REPLY_NOW.value:
                messages = await self._prompt(action["conversation_id"])
                reply = await self.ai.chat(messages, temperature=0.85)
                await self.memory.schedule(
                    "reply",
                    {
                        "conversation_id": action["conversation_id"],
                        "persona_id": self.persona["id"],
                        "text": reply,
                    },
                    time() + 1,
                )

        return actions

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

    def _delay_seconds(self, state: MoodState, text: str) -> float:
        delay_config = self.persona.get("availability", {}).get("delay_seconds", {})
        minimum = float(delay_config.get("min", 4))
        maximum = float(delay_config.get("max", 900))
        median = float(delay_config.get("median", 45))
        base = median + min(len(text) * 0.8, median)
        arousal = state.values.get("arousal", 0)
        return min(maximum, max(minimum, base * (1.0 + max(0.0, -arousal))))