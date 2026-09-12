import random
from time import time
import time as time_module
from app.core.logger import get_logger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.domain.mood import MoodEngine, MoodState
from app.infrastructure.memory import ShortTermMemory
from app.domain.models import Decision
from app.domain.behavior import BehaviorContext, BehaviorEngine
from app.agents.persona_graph import PersonaAgentGraph

from app.infrastructure.sqlite import (
    clear_conversation,
    delete_message,
    get_unread_user_messages,
    get_conversation,
    get_inactive_conversations,
    get_last_message,
    mark_message_seen_for_persona,
    save_message,
)

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, persona, redis) -> None:
        self.persona = persona

        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)
        self.short_memory = ShortTermMemory(redis)
        self.agent = PersonaAgentGraph(persona, redis)

        self.states: dict[str, MoodState] = {}

        logger.debug(f"[ConversationService.__init__] Initialized service for {persona.get('profile', {}).get('name', 'unknown')}")

    def update_persona(self, persona: dict) -> None:
        self.persona = persona
        self.behavior = BehaviorEngine(persona)
        self.mood = MoodEngine(persona)
        self.agent.update_persona(persona)

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

            # Presence is the input to the behaviour decision, never an
            # effect of generating a reply.  Initialise it if the worker has
            # not yet performed its first life tick.
            presence = await self.get_presence()
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
                hour=self._local_now().hour,
                idle_minutes=0,
                unread=True,
                online=bool(presence.get("online")),
                busy=bool(presence.get("busy")),
            )

            decision = self.behavior.decide(state, ctx)
            logger.info(f"[ConversationService.ingest] Behavior decision made: {decision.value}, online={ctx.online}, hour={ctx.hour}")

            if decision is Decision.NO_REPLY:
                logger.info(f"[ConversationService.ingest] Decision: NO_REPLY for conversation_id={conversation_id}")
                return {"message_id": message_id}

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

                return {"message_id": message_id}

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

            return {"message_id": message_id}
        except Exception as e:
            logger.error(f"[ConversationService.ingest] Error during ingest: {e}", exc_info=True)
            raise

    async def generate_reply(self, conversation_id: str, initiative: str | None = None) -> str:
        logger.info(f"[ConversationService.generate_reply] Generating reply for conversation_id={conversation_id}, persona_id={self.persona['id']}")
        try:
            logger.debug(f"[ConversationService.generate_reply] Building prompt for conversation_id={conversation_id}")
            logger.debug(f"[ConversationService.generate_reply] Publishing typing indicator")
            await self.short_memory.publish_typing(self.persona["id"], conversation_id, True)

            try:
                logger.debug(f"[ConversationService.generate_reply] Invoking persona LangGraph")
                start = time_module.time()
                state = self.states.get(conversation_id) or await self._load_state()
                self.states[conversation_id] = state
                reply = await self.agent.generate_reply(
                    conversation_id,
                    state.values,
                    initiative=initiative,
                )
                elapsed = time_module.time() - start

                logger.info(f"[ConversationService.generate_reply] AI reply generated in {elapsed:.2f}s: conversation_id={conversation_id}, reply_length={len(reply)}")
                return reply
            finally:
                logger.debug(f"[ConversationService.generate_reply] Clearing typing indicator")
                await self.short_memory.publish_typing(self.persona["id"], conversation_id, False)
        except Exception as e:
            logger.error(f"[ConversationService.generate_reply] Error generating reply: {e}", exc_info=True)
            raise

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
            local_now = self._local_now()
            probability = self.behavior.online_probability(local_now.hour, state)
            logger.debug(f"[ConversationService.life_tick] Online probability at local hour {local_now.hour}: {probability:.2%}")
            
            previous = await self.short_memory.get_presence(self.persona["id"])
            online = random.random() < probability
            if previous and previous.get("online") and random.random() < 0.75:
                online = True
                logger.debug(f"[ConversationService.life_tick] Persona stays online with 75% probability")

            busy = online and random.random() < self.behavior.busy_probability(local_now.hour, state)

            changed = (
                not previous
                or previous.get("online") != online
                or previous.get("busy") != busy
            )
            logger.debug(f"[ConversationService.life_tick] Presence changed: {changed}, was_online={previous.get('online') if previous else 'unknown'}, now_online={online}")
            
            presence = {
                "online": online,
                "busy": busy,
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
                        hour=self._local_now().hour,
                        idle_minutes=idle_minutes,
                        unread=True,
                        online=True,
                        busy=bool((await self.get_presence()).get("busy")),
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
                            "user_message_id": action["message_id"],
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
                            "user_message_id": action["message_id"],
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
            event_name = await self.agent.classify_event(text, allowed_events)
            logger.debug(f"[ConversationService._classify_event] AI selected event: {event_name}")
            return event_name
        except Exception as exc:
            logger.warning(f"[ConversationService._classify_event] AI event classification failed, falling back to keyword matching: {exc}")

        return allowed_events[0]

    def _delay_seconds(self, state: MoodState, text: str) -> float:
        delay_config = self.persona.get("availability", {}).get("delay_seconds", {})
        minimum = float(delay_config.get("min", 4))
        maximum = float(delay_config.get("max", 900))
        median = float(delay_config.get("median", 45))
        base = median + min(len(text) * 0.8, median)
        arousal = state.values.get("arousal", 0)
        return min(maximum, max(minimum, base * (1.0 + max(0.0, -arousal))))

    async def schedule_self_follow_ups(self) -> int:
        """Queue a natural, persona-configured initiation for an idle chat."""
        cfg = self.persona.get("self_trigger", {})
        if not cfg.get("enabled"):
            return 0

        presence = await self.get_presence()
        if not presence.get("online") or presence.get("busy"):
            return 0

        local_now = self._local_now()
        configured_windows = cfg.get("time_windows", [])
        windows = list(configured_windows) if isinstance(configured_windows, list) else []
        windows.extend(
            window
            for name in ("morning_window", "midday_window", "evening_window")
            if isinstance(window := cfg.get(name), list) and len(window) == 2
        )
        if windows and not any(int(window[0]) <= local_now.hour <= int(window[1]) for window in windows):
            return 0

        idle_config = cfg.get("idle_minutes_before_follow_up", cfg.get("minimum_idle_minutes", 4320))
        minimum_idle = self._minimum_duration(idle_config, default=4320)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minimum_idle)
        candidates = await get_inactive_conversations(self.persona["id"], cutoff)
        if not candidates:
            return 0

        daily_budget = max(0, int(cfg.get("daily_budget", 1)))
        day_key = local_now.date().isoformat()
        sent_key = f"persona:follow-up:count:{self.persona['id']}:{day_key}"
        triggers = cfg.get("triggers", [])
        valid_triggers = [t for t in triggers if isinstance(t, dict) and t.get("type")]
        if not valid_triggers:
            return 0

        random.shuffle(candidates)
        candidate = None
        for possible_candidate in candidates:
            threshold = await self._follow_up_threshold(
                possible_candidate["conversation_id"],
                possible_candidate["last_user_at"],
                idle_config,
            )
            last_user_at = possible_candidate["last_user_at"]
            if last_user_at.tzinfo is None:
                last_user_at = last_user_at.replace(tzinfo=timezone.utc)
            idle_minutes = (datetime.now(timezone.utc) - last_user_at).total_seconds() / 60
            if idle_minutes >= threshold:
                candidate = possible_candidate
                break
        if candidate is None:
            return 0

        conversation_id = candidate["conversation_id"]
        cooldown = self._sample_duration(cfg.get("cooldown_minutes"), default=minimum_idle)
        # A Redis lease prevents every 30-second life tick from scheduling a
        # duplicate initiation for the same inactive conversation.
        if not await self.short_memory.claim_follow_up(
            self.persona["id"], conversation_id, int(cooldown * 60)
        ):
            return 0
        if not await self.short_memory.reserve_daily_follow_up(sent_key, daily_budget):
            await self.short_memory.release_follow_up_claim(self.persona["id"], conversation_id)
            return 0

        weights = [max(0.0, float(trigger.get("weight", 0))) for trigger in valid_triggers]
        trigger = random.choices(valid_triggers, weights=weights if any(weights) else None, k=1)[0]
        delay = self._sample_duration(cfg.get("delay_seconds"), default=60)
        await self.short_memory.schedule(
            "follow_up",
            {
                "conversation_id": conversation_id,
                "persona_id": self.persona["id"],
                "trigger": trigger["type"],
            },
            time() + delay,
        )
        logger.info(
            "[ConversationService.schedule_self_follow_ups] Scheduled %s for %s",
            trigger["type"], conversation_id,
        )
        return 1

    async def _follow_up_threshold(self, conversation_id, last_user_at, config) -> float:
        """Keep a sampled threshold stable until the human messages again."""
        memory_key = f"persona:follow-up:threshold:{self.persona['id']}:{conversation_id}"
        last_user_key = last_user_at.isoformat()
        stored = await self.short_memory.get_json(memory_key)
        if stored and stored.get("last_user_at") == last_user_key:
            return float(stored["idle_minutes"])

        threshold = self._sample_duration(config, default=4320)
        await self.short_memory.set_json(
            memory_key,
            {"last_user_at": last_user_key, "idle_minutes": threshold},
            ttl=max(60, int(threshold * 120)),
        )
        return threshold

    @staticmethod
    def _minimum_duration(config, default: float) -> float:
        if isinstance(config, dict):
            return max(1.0, float(config.get("min", default)))
        if isinstance(config, (int, float)):
            return max(1.0, float(config))
        return default

    @staticmethod
    def _sample_duration(config, default: float) -> float:
        if isinstance(config, (int, float)):
            return max(1.0, float(config))
        if not isinstance(config, dict):
            return default

        minimum = max(1.0, float(config.get("min", default)))
        maximum = max(minimum, float(config.get("max", minimum)))
        mode = min(maximum, max(minimum, float(config.get("mode", (minimum + maximum) / 2))))
        return random.triangular(minimum, maximum, mode)

    def _local_now(self) -> datetime:
        timezone_name = self.persona.get("profile", {}).get("timezone", "UTC")
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            logger.warning("Unknown persona timezone %r; using UTC", timezone_name)
            return datetime.now(timezone.utc)
