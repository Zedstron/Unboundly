import random
from time import time
import time as time_module
from app.core.config import settings
from app.core.logger import get_logger
from app.core.prompts import get_prompt
from datetime import datetime, timezone
from app.infrastructure.ai import AIProvider
from app.domain.mood import MoodEngine, MoodState
from app.infrastructure.memory import LongTermMemory
from app.infrastructure.memory import ShortTermMemory
from app.domain.models import AgentResponse, Decision
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

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, persona, redis) -> None:
        self.persona = persona

        self.ai = AIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            embedding_model=settings.embedding_model,
            embedding_base_url=settings.embedding_base_url,
            embedding_api_key=settings.embedding_api_key,
        )

        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)

        self.short_memory = ShortTermMemory(redis)
        self.long_memory = LongTermMemory(redis, self.ai)

        self.states: dict[str, MoodState] = {}

        logger.debug(f"[ConversationService.__init__] Initialized service for {persona.get('profile', {}).get('name', 'unknown')}")

    def update_persona(self, persona: dict) -> None:
        self.persona = persona
        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)

    async def get_conversation(self, conversation_id: str):
        return await get_conversation(self.persona["id"], conversation_id)

    async def get_last_message(self, conversation_id: str):
        return await get_last_message(self.persona["id"], conversation_id)

    async def delete_message(self, conversation_id: str, message_id: int) -> bool:
        return await delete_message(self.persona["id"], conversation_id, message_id)

    async def clear_conversation(self, conversation_id: str) -> int:
        return await clear_conversation(self.persona["id"], conversation_id)

    async def ingest(self, conversation_id: str, text: str) -> dict:
        logger.info(f"[ConversationService.ingest] Starting ingestion: persona_id={self.persona['id']}, conversation_id={conversation_id}, text_length={len(text)}")
        try:
            now = datetime.now(timezone.utc)

            logger.debug(f"[ConversationService.ingest] Loading mood state for persona_id={self.persona['id']}")
            state = await self._load_state()

            logger.debug(f"[ConversationService.ingest] Classifying event")
            event = await self._classify_event(text)
            logger.debug(f"[ConversationService.ingest] Event classified as: {event}")
            
            state = self.mood.update(state, event)
            logger.debug(f"[ConversationService.ingest] Mood state updated: {state.values}")

            self.states[conversation_id] = state

            await self._save_state(state)
            logger.debug(f"[ConversationService.ingest] Mood state saved for persona_id={self.persona['id']}")

            presence = await self.short_memory.get_presence(self.persona["id"])
            logger.debug(f"[ConversationService.ingest] Presence retrieved: online={presence.get('online') if presence else 'unknown'}")

            message_id = await save_message(
                self.persona["id"],
                conversation_id,
                "user",
                text,
                "delivered",
            )
            logger.debug(f"[ConversationService.ingest] User message saved: message_id={message_id}")

            ctx = BehaviorContext(
                hour=now.hour,
                idle_minutes=0,
                unread=False,
                online=bool(presence and presence.get("online")),
            )

            decision = self.behavior.decide(state, ctx)
            logger.info(f"[ConversationService.ingest] Behavior decision made: {decision.value}, online={ctx.online}, hour={ctx.hour}")

            if decision is Decision.NO_REPLY:
                logger.info(f"[ConversationService.ingest] Decision: NO_REPLY for conversation_id={conversation_id}")
                return {
                    "decision": decision.value,
                    "message_id": message_id
                }

            if decision is Decision.LATE_REPLY:
                due = time() + self._delay_seconds(state, text)
                logger.info(f"[ConversationService.ingest] Decision: LATE_REPLY, scheduling for {due - time():.1f} seconds from now")

                await self.short_memory.schedule(
                    "reply_pending",
                    {
                        "conversation_id": conversation_id,
                        "persona_id": self.persona["id"],
                        "user_message_id": message_id
                    },
                    due,
                )
                logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task: conversation_id={conversation_id}")

                return { "decision": decision.value, "message_id": message_id }

            logger.info(f"[ConversationService.ingest] Decision: REPLY_NOW, generating response")
            reply = await self.generate_reply(conversation_id)

            await self.short_memory.schedule(
                "reply",
                {
                    "conversation_id": conversation_id,
                    "persona_id": self.persona["id"],
                    "user_message_id": message_id,
                    "text": reply
                },
                time() + 1,
            )
            logger.info(f"[ConversationService.ingest] Scheduled reply task immediately: conversation_id={conversation_id}, reply_length={len(reply)}")

            return { "decision": decision.value, "message_id": message_id }
        except Exception as e:
            logger.error(f"[ConversationService.ingest] Error during ingest: {e}", exc_info=True)
            raise

    async def generate_reply(self, conversation_id: str) -> str:
        logger.info(f"[ConversationService.generate_reply] Generating reply for conversation_id={conversation_id}, persona_id={self.persona['id']}")
        try:
            logger.debug(f"[ConversationService.generate_reply] Building prompt for conversation_id={conversation_id}")
            messages = await self._prompt(conversation_id)
            logger.debug(f"[ConversationService.generate_reply] Prompt built with {len(messages)} messages")

            logger.debug(f"[ConversationService.generate_reply] Publishing typing indicator")
            await self.short_memory.publish_typing(self.persona["id"], conversation_id, True)

            try:
                logger.debug(f"[ConversationService.generate_reply] Calling AI chat API")
                start = time_module.time()

                result = await self.ai.chat(messages, temperature=0.85)
                await self._save_memories(conversation_id, result)

                reply = result.response
                elapsed = time_module.time() - start

                logger.info(f"[ConversationService.generate_reply] AI reply generated in {elapsed:.2f}s: conversation_id={conversation_id}, reply_length={len(reply)}")
                return reply
            finally:
                logger.debug(f"[ConversationService.generate_reply] Clearing typing indicator")
                await self.short_memory.publish_typing(self.persona["id"], conversation_id, False)
        except Exception as e:
            logger.error(f"[ConversationService.generate_reply] Error generating reply: {e}", exc_info=True)
            raise

    async def _prompt(self, conversation_id: str) -> list[dict[str, str]]:
        roles = { "bot": "assistant", "user": "user" }

        history = await get_conversation(self.persona["id"], conversation_id, limit=15)

        short_memories = await self.short_memory.get_short_memories(self.persona["id"] + conversation_id)

        long_memories = []
        if self.long_memory is not None and len(history) > 0:
            long_memories = await self.long_memory.search(self.persona["id"], history[-1]["content"])

        state = self.states.get(conversation_id) or await self._load_state()
        self.states[conversation_id] = state

        system = get_prompt(
            "persona",
            {
                "name": self.persona.get("profile", {}).get("name", ""),
                "profile": self.persona.get("profile", {}),
                "traits": self.persona.get("traits", ""),
                "mood": state.values,
                "short_memories": short_memories,
                "long_memories": long_memories,
                "language_style": self.persona.get("language_style", ""),
            },
        )

        history = [ { "role": roles[m["direction"]], "content": m["content"] } for m in history ]

        return [{"role": "system", "content": system}] + history

    async def _save_memories(self, conversation_id, result: AgentResponse) -> None:
        for memory in result.memories:
            payload = memory.model_dump()

            if memory.lifetime == "ephemeral":
                continue

            if memory.lifetime == "short":
                await self.short_memory.save_short_memory(self.persona["id"] + conversation_id, payload)

            elif self.long_memory is not None:
                await self.long_memory.save(self.persona["id"] + conversation_id, payload)

    async def _load_state(self) -> MoodState:
        stored = await self.short_memory.get_persona_state(self.persona["id"])
        if stored:
            return MoodState(
                values={key: float(value) for key, value in stored["mood"].items()},
                updated_at=datetime.fromisoformat(stored["updated_at"]),
            )

        return MoodState(dict(self.persona["mood"]["baseline"]), datetime.now(timezone.utc))

    async def _save_state(self, state: MoodState) -> None:
        await self.short_memory.set_persona_state(
            self.persona["id"],
            { 
                "mood": state.values,
                "updated_at": state.updated_at.isoformat()
            }
        )

    async def life_tick(self) -> dict:
        logger.debug(f"[ConversationService.life_tick] Running life_tick for persona_id={self.persona['id']}")
        try:
            state = await self._load_state()
            logger.debug(f"[ConversationService.life_tick] Current state loaded: {state.values}")
            
            state = self.mood.update(state, "time_passed")
            logger.debug(f"[ConversationService.life_tick] Mood updated after time passage: {state.values}")
            
            await self._save_state(state)

            now = datetime.now(timezone.utc)
            probability = self.behavior.online_probability(now.hour, state)
            logger.debug(f"[ConversationService.life_tick] Online probability at hour {now.hour}: {probability:.2%}")
            
            previous = await self.short_memory.get_presence(self.persona["id"])
            online = random.random() < probability
            if previous and previous.get("online") and random.random() < 0.75:
                online = True
                logger.debug(f"[ConversationService.life_tick] Persona stays online with 75% probability")

            changed = not previous or previous.get("online") != online
            logger.debug(f"[ConversationService.life_tick] Presence changed: {changed}, was_online={previous.get('online') if previous else 'unknown'}, now_online={online}")
            
            presence = {
                "online": online,
                "last_seen": now.timestamp() if online else (previous or {}).get("last_seen", now.timestamp()),
                "probability": probability,
                "updated_at": now.timestamp(),
            }
            await self.short_memory.set_presence(self.persona["id"], presence)
            logger.info(f"[ConversationService.life_tick] Presence updated: online={online}, probability={probability:.2%}")
            
            became_online = online and not (previous or {}).get("online", False)
            logger.debug(f"[ConversationService.life_tick] Became online: {became_online}")
            
            return {
                "presence": presence,
                "changed": changed,
                "became_online": became_online,
            }
        except Exception as e:
            logger.error(f"[ConversationService.life_tick] Error in life_tick: {e}", exc_info=True)
            raise

    async def get_presence(self) -> dict:
        presence = await self.short_memory.get_presence(self.persona["id"])
        if presence:
            return presence
        result = await self.life_tick()
        return result["presence"]

    async def process_unread_messages(self) -> list[dict]:
        logger.info(f"[ConversationService.process_unread_messages] Processing unread messages for persona_id={self.persona['id']}")
        try:
            now = datetime.now(timezone.utc)
            actions = []
            state = await self._load_state()
            logger.debug(f"[ConversationService.process_unread_messages] Loaded state: {state.values}")

            unread_messages = await get_unread_user_messages(self.persona["id"])
            logger.debug(f"[ConversationService.process_unread_messages] Found {len(unread_messages)} unread messages")

            for message in unread_messages:
                message_id = await mark_message_seen_for_persona(
                    self.persona["id"], message["conversation_id"], message["id"]
                )
                if not message_id:
                    logger.warning(f"[ConversationService.process_unread_messages] Failed to mark message as seen: message_id={message['id']}")
                    continue

                logger.debug(f"[ConversationService.process_unread_messages] Processing message: message_id={message['id']}, conversation_id={message['conversation_id']}, content={message['content'][:50]}...")
                
                event = await self._classify_event(message["content"])
                state = self.mood.update(state, event)
                self.states[message["conversation_id"]] = state
                logger.debug(f"[ConversationService.process_unread_messages] Event classified as: {event}, mood updated: {state.values}")

                created_at = message["created_at"]

                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                idle_minutes = max(0.0, (now - created_at).total_seconds() / 60)
                logger.debug(f"[ConversationService.process_unread_messages] Message idle time: {idle_minutes:.1f} minutes")
                
                decision = self.behavior.decide(
                    state,
                    BehaviorContext(
                        hour=now.hour,
                        idle_minutes=idle_minutes,
                        unread=True,
                        online=True,
                    ),
                )
                logger.debug(f"[ConversationService.process_unread_messages] Behavior decision: {decision.value}")
                
                actions.append({
                    "message_id": message_id,
                    "conversation_id": message["conversation_id"],
                    "decision": decision.value,
                    "content": message["content"],
                })

            await self._save_state(state)
            logger.debug(f"[ConversationService.process_unread_messages] State saved after processing all messages")

            logger.debug(f"[ConversationService.process_unread_messages] Processing {len(actions)} actions")
            for action in actions:
                if action["decision"] == Decision.LATE_REPLY.value:
                    delay = self._delay_seconds(state, action["content"])
                    logger.info(f"[ConversationService.process_unread_messages] Scheduling LATE_REPLY: conversation_id={action['conversation_id']}, delay_seconds={delay:.1f}")
                    
                    await self.short_memory.schedule(
                        "reply",
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                        },
                        time() + delay,
                    )
                elif action["decision"] == Decision.REPLY_NOW.value:
                    logger.info(f"[ConversationService.process_unread_messages] Generating immediate reply: conversation_id={action['conversation_id']}")
                    
                    reply = await self.generate_reply(action["conversation_id"])
                    logger.debug(f"[ConversationService.process_unread_messages] Reply generated: length={len(reply)}")
                    
                    await self.short_memory.schedule(
                        "reply",
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                            "text": reply,
                        },
                        time() + 1,
                    )
                    logger.debug(f"[ConversationService.process_unread_messages] Scheduled reply task")
                else:
                    logger.debug(f"[ConversationService.process_unread_messages] No action needed: decision={action['decision']}")

            logger.info(f"[ConversationService.process_unread_messages] Processed {len(actions)} unread messages")
            return actions
        except Exception as e:
            logger.error(f"[ConversationService.process_unread_messages] Error processing unread messages: {e}", exc_info=True)
            raise

    async def _classify_event(self, text: str) -> str:
        if not text or not text.strip():
            return "long_idle_gap"

        allowed_events = list(dict.fromkeys(self.mood.config.get("event_weights", {}).keys()))
        if not allowed_events:
            allowed_events = ["reassurance", "compliment", "conflict", "long_idle_gap"]

        try:
            event_name = await self.ai.classify_event(text, allowed_events)
            logger.debug(f"[ConversationService._classify_event] AI selected event: {event_name}")
            return event_name
        except Exception as exc:
            logger.warning(f"[ConversationService._classify_event] AI event classification failed, falling back to keyword matching: {exc}")

        return self._fallback_classify_event(text)

    @staticmethod
    def _fallback_classify_event(text: str) -> str:
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