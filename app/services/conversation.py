import random
from time import time
from app.domain.models import Decision
from datetime import datetime, timezone
from app.core.logger import get_logger
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

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, persona, memory: RedisMemory, ai: OpenAICompatibleAI) -> None:
        self.persona = persona
        self.memory = memory
        self.ai = ai
        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)
        self.states: dict[str, MoodState] = {}
        logger.debug(f"[ConversationService.__init__] Initialized service for persona_id={persona['id']}, name={persona.get('profile', {}).get('name', 'unknown')}")

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

            logger.debug(f"[ConversationService.ingest] Classifying event: text={text[:50]}...")
            event = self._classify_event(text)
            logger.debug(f"[ConversationService.ingest] Event classified as: {event}")
            
            state = self.mood.update(state, event)
            logger.debug(f"[ConversationService.ingest] Mood state updated: {state.values}")

            self.states[conversation_id] = state
            await self._save_state(state)
            logger.debug(f"[ConversationService.ingest] Mood state saved for persona_id={self.persona['id']}")

            presence = await self.memory.get_presence(self.persona["id"])
            logger.debug(f"[ConversationService.ingest] Presence retrieved: online={presence.get('online') if presence else 'unknown'}")

            await self.memory.append_short_term(
                conversation_id,
                {
                    "role": "user",
                    "content": text,
                    "ts": time(),
                },
            )
            logger.debug(f"[ConversationService.ingest] Message added to short-term memory")

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
                    "message_id": message_id,
                }

            if decision is Decision.LATE_REPLY:
                due = time() + self._delay_seconds(state, text)
                logger.info(f"[ConversationService.ingest] Decision: LATE_REPLY, scheduling for {due - time():.1f} seconds from now")

                await self.memory.schedule(
                    "reply_pending",
                    {
                        "conversation_id": conversation_id,
                        "persona_id": self.persona["id"],
                        "user_message_id": message_id,
                    },
                    due,
                )
                logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task: conversation_id={conversation_id}")

                return { "decision": decision.value, "message_id": message_id }

            logger.info(f"[ConversationService.ingest] Decision: REPLY_NOW, generating response")
            reply = await self.generate_reply(conversation_id)

            await self.memory.schedule(
                "reply",
                {
                    "conversation_id": conversation_id,
                    "persona_id": self.persona["id"],
                    "text": reply,
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
            await self.memory.publish_typing(self.persona["id"], conversation_id, True)

            try:
                logger.debug(f"[ConversationService.generate_reply] Calling AI chat API")
                import time as time_module
                start = time_module.time()
                reply = await self.ai.chat(messages, temperature=0.85)
                elapsed = time_module.time() - start
                logger.info(f"[ConversationService.generate_reply] AI reply generated in {elapsed:.2f}s: conversation_id={conversation_id}, reply_length={len(reply)}")
                return reply
            finally:
                logger.debug(f"[ConversationService.generate_reply] Clearing typing indicator")
                await self.memory.publish_typing(self.persona["id"], conversation_id, False)
        except Exception as e:
            logger.error(f"[ConversationService.generate_reply] Error generating reply: {e}", exc_info=True)
            raise

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
            
            previous = await self.memory.get_presence(self.persona["id"])
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
            await self.memory.set_presence(self.persona["id"], presence)
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
        presence = await self.memory.get_presence(self.persona["id"])
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
                
                event = self._classify_event(message["content"])
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
                    
                    await self.memory.schedule(
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
                    
                    await self.memory.schedule(
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