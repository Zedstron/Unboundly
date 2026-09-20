import json
import random
from typing import Any
from time import time
import time as time_module
from app.core.logger import get_logger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.domain.mood import MoodEngine, MoodState
from app.infrastructure.memory import ShortTermMemory
from app.domain.models import Decision
from app.services.bridges.models import SocialMessage, Operation
from app.domain.behavior import BehaviorContext, BehaviorEngine
from app.agents.persona_graph import PersonaAgentGraph
from app.services.bridges.registry import registry

from app.infrastructure.sqlite import (
    clear_conversation,
    delete_message,
    get_unread_user_messages,
    get_conversation,
    get_inactive_conversations,
    get_last_message,
    mark_message_seen_for_persona,
    mark_messages_seen,
    save_message,
    save_persona_state,
    get_persona_state as get_persona_state_db,
    reset_persona_state as reset_persona_state_db,
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
        self.current_state: MoodState | None = None

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

    async def __bridge_signal(self, operation: Operation, source=None, **kwargs):
        if source and source != "local":
            future = registry.get(source).signal(self.persona["id"], operation, **kwargs)
            if future is not None:
                try:
                    await future
                except Exception:
                    logger.exception("Failed bridge operation %s for persona %s", operation, self.persona["id"])

    async def mark_conversation_seen(self, conversation_id: str, source: str = None, up_to_message_id: int | None = None) -> list[int]:
        seen_ids = await mark_messages_seen(
            conversation_id,
            persona_id=self.persona["id"],
            up_to_message_id=up_to_message_id,
        )
        if seen_ids:
            channel = f"persona:out:{self.persona['id']}:{conversation_id}"
            for mid in seen_ids:
                logger.debug(f"[ConversationService.mark_conversation_seen] Publishing seen status: message_id={mid}, conversation_id={conversation_id}")

                await self.short_memory.redis.publish(
                    channel,
                    json.dumps({
                        "type": "status",
                        "persona_id": self.persona["id"],
                        "conversation_id": conversation_id,
                        "message_id": mid,
                        "status": "seen",
                    }),
                )

            await self.__bridge_signal(Operation.MARK_SEEN, source=source, chat_id=conversation_id)
        return seen_ids

    async def ingest(self, message: SocialMessage) -> dict:
        logger.info(f"[ConversationService.ingest] Starting ingestion: persona_id={self.persona['id']}, conversation_id={message.chat_id}, text_length={len(message.text)}")
        try:
            logger.debug(f"[ConversationService.ingest] Loading mood state for persona_id={self.persona['id']}")
            state = await self._load_state()

            logger.debug(f"[ConversationService.ingest] Classifying event")
            event = await self._classify_event(message.text)
            logger.debug(f"[ConversationService.ingest] Event classified as: {event}")
            
            state = self.mood.update(state, event)
            logger.debug(f"[ConversationService.ingest] Mood state updated: {state.values}")

            self.states[message.chat_id] = state

            await self._save_state(state)
            logger.debug(f"[ConversationService.ingest] Mood state saved for persona_id={self.persona['id']}")

            # Presence is the input to the behaviour decision, never an
            # effect of generating a reply.  Initialise it if the worker has
            # not yet performed its first life tick.
            presence = await self.get_presence()
            logger.debug(f"[ConversationService.ingest] Presence retrieved: online={presence.get('online') if presence else 'unknown'}")

            message_id = await save_message(
                self.persona["id"],
                message.chat_id,
                "user",
                message.text,
                "delivered",
                source=message.provider,
                external_id=message.message_id,
                sender_id=message.sender_id,
                sender_name=message.sender_name
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
                logger.info(f"[ConversationService.ingest] Decision: NO_REPLY for conversation_id={message.chat_id}")
                return { "message_id": message_id }

            if decision is Decision.LATE_REPLY:
                due = time() + self._delay_seconds(state, message.text)
                logger.info(f"[ConversationService.ingest] Decision: LATE_REPLY, scheduling for {due - time():.1f} seconds from now")

                await self.short_memory.schedule(
                    "reply_pending",
                    {
                        "conversation_id": message.chat_id,
                        "persona_id": self.persona["id"],
                        "user_message_id": message_id,
                        "source": message.provider,
                        "sender_id": message.sender_id
                    },
                    due,
                )
                logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task: conversation_id={message.chat_id}")

                return { "message_id": message_id }

            logger.info(f"[ConversationService.ingest] Decision: REPLY_NOW, scheduling immediate reply")
            await self.short_memory.schedule(
                "reply_pending",
                {
                    "conversation_id": message.chat_id,
                    "persona_id": self.persona["id"],
                    "user_message_id": message_id,
                    "source": message.provider,
                    "sender_id": message.sender_id
                },
                time(),
            )
            logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task immediately: conversation_id={message.chat_id}")

            return { "message_id": message_id }
        except Exception as e:
            logger.error(f"[ConversationService.ingest] Error during ingest: {e}", exc_info=True)
            raise

    async def generate_reply(self, conversation_id: str, initiative: str | None = None, source: str = None) -> str:
        logger.info(f"[ConversationService.generate_reply] Generating reply for conversation_id={conversation_id}, persona_id={self.persona['id']}")
        try:
            logger.debug(f"[ConversationService.generate_reply] Marking conversation messages as seen before typing")
            await self.mark_conversation_seen(conversation_id, source=source)

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
        try:
            db_state = await get_persona_state_db(self.persona["id"])
            if db_state:
                baseline = self.persona.get("mood", {}).get("baseline", {})
                values = {**baseline, **db_state["mood"]}
                state = MoodState(values=values, updated_at=db_state["updated_at"])
                self.current_state = state
                try:
                    await self.short_memory.set_persona_state(
                        self.persona["id"],
                        {
                            "mood": state.values,
                            "updated_at": state.updated_at.isoformat(),
                        },
                    )
                except Exception:
                    pass

                return state
        except Exception as e:
            logger.warning(f"[ConversationService._load_state] Failed to load state from SQLite for persona_id={self.persona['id']}: {e}")


        try:
            stored = await self.short_memory.get_persona_state(self.persona["id"])
            if stored:
                baseline = self.persona.get("mood", {}).get("baseline", {})
                stored_mood = {key: float(value) for key, value in stored.get("mood", {}).items()}
                values = {**baseline, **stored_mood}
                updated_at = datetime.fromisoformat(stored["updated_at"])
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                state = MoodState(values=values, updated_at=updated_at)
                self.current_state = state
                try:
                    await save_persona_state(self.persona["id"], state.values, state.updated_at)
                except Exception:
                    pass
                return state
        except Exception as e:
            logger.warning(f"[ConversationService._load_state] Failed to load state from Redis for persona_id={self.persona['id']}: {e}")


        state = MoodState(dict(self.persona["mood"]["baseline"]), datetime.now(timezone.utc))
        self.current_state = state
        return state

    async def _save_state(self, state: MoodState) -> None:
        self.current_state = state
        try:
            await save_persona_state(
                self.persona["id"],
                state.values,
                state.updated_at,
            )
        except Exception as e:
            logger.error(f"[ConversationService._save_state] Failed to save state to SQLite for persona_id={self.persona['id']}: {e}")

        try:
            await self.short_memory.set_persona_state(
                self.persona["id"],
                { 
                    "mood": state.values,
                    "updated_at": state.updated_at.isoformat()
                }
            )
        except Exception as e:
            logger.warning(f"[ConversationService._save_state] Failed to save state to Redis for persona_id={self.persona['id']}: {e}")

    async def load_state(self) -> MoodState:
        return await self._load_state()

    async def reset_state(self) -> MoodState:
        try:
            await reset_persona_state_db(self.persona["id"])
        except Exception as e:
            logger.error(f"[ConversationService.reset_state] Error resetting state in SQLite: {e}")

        try:
            await self.short_memory.delete_persona_state(self.persona["id"])
        except Exception as e:
            logger.warning(f"[ConversationService.reset_state] Error deleting state from Redis: {e}")

        baseline = dict(self.persona.get("mood", {}).get("baseline", {}))
        self.current_state = MoodState(baseline, datetime.now(timezone.utc))
        self.states.clear()
        logger.info(f"[ConversationService.reset_state] Mood state reset to baseline for persona_id={self.persona['id']}")
        return self.current_state

    async def set_mood(self, mood_values: dict[str, float]) -> MoodState:
        current = self.current_state or await self._load_state()
        new_values = dict(current.values)

        for k, v in mood_values.items():
            new_values[k] = float(v)

        new_state = MoodState(new_values, datetime.now(timezone.utc))

        await self._save_state(new_state)
        return new_state

    async def get_state(self) -> dict[str, Any]:
        state = self.current_state or await self._load_state()
        return {
            "persona_id": self.persona["id"],
            "mood": state.values,
            "updated_at": state.updated_at.isoformat(),
            "baseline": self.persona.get("mood", {}).get("baseline", {}),
        }

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
                        "reply_pending",
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                            "user_message_id": action["message_id"],
                        },
                        time() + delay,
                    )
                elif action["decision"] == Decision.REPLY_NOW.value:
                    logger.info(f"[ConversationService.process_unread_messages] Scheduling immediate reply: conversation_id={action['conversation_id']}")
                    await self.short_memory.schedule(
                        "reply_pending",
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                            "user_message_id": action["message_id"],
                        },
                        time(),
                    )
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
