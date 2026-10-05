import json
import random
from typing import Any
from time import time
import time as time_module
from app.core.logger import get_logger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.domain.mood import MoodEngine, MoodState
from app.domain.awareness import (
    PesterLevel,
    annoyance_increment,
    decay_annoyance,
    evaluate_pestering,
    is_at_least,
    mood_event,
    select_confidant,
)
from app.infrastructure.memory import ShortTermMemory
from app.domain.models import AgentResponse, Commitment, Decision
from app.domain.scheduling import defer_within_window, resolve_schedule
from app.services.bridges.models import SocialMessage, Operation
from app.domain.behavior import BehaviorContext, BehaviorEngine
from app.agents.persona_graph import PersonaAgentGraph
from app.services.bridges.registry import registry

from app.core.config import settings
from app.infrastructure.sqlite import (
    clear_conversation,
    delete_message,
    get_unread_user_messages,
    get_conversation,
    get_inactive_conversations,
    get_last_message,
    get_last_user_message,
    mark_message_seen_for_persona,
    mark_messages_seen,
    save_message,
    save_persona_state,
    get_persona_state as get_persona_state_db,
    reset_persona_state as reset_persona_state_db,
    get_contact,
    get_message_by_external_id,
    count_unanswered_user_messages,
    find_conversation_by_sender,
    list_contacts,
    update_contact_annoyance,
    cancel_open_commitments,
    claim_due_commitments,
    get_commitment,
    recover_stale_commitments,
    reschedule_commitment,
    save_commitment,
    settle_commitment as settle_commitment_db,
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
            if source is None or source == "local":
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

            if source and source != "local":
                await self.__bridge_signal(Operation.MARK_SEEN, source=source, chat_id=conversation_id)
        return seen_ids


    async def _contact_is_unknown(
        self,
        conversation_id: str,
        sender_id: str | None = None,
    ) -> bool:
        """A conversation is unknown unless a vetted (non-unknown) contact matches it.

        Checks both the sender-scoped contact and the conversation-scoped one,
        treating the conversation as known if either has been vetted. Contacts
        auto-created by the reply pipeline keep ``is_unknown=True`` until they
        are explicitly saved with a real name.
        """
        persona_id = self.persona["id"]
        candidate_ids = [conversation_id]
        if sender_id and sender_id != conversation_id:
            candidate_ids.insert(0, sender_id)

        for contact_id in candidate_ids:
            contact = await get_contact(persona_id, contact_id)
            if contact is not None and not bool(contact.get("is_unknown")):
                return False

        return True

    async def ingest(self, message: SocialMessage) -> dict:
        logger.info(f"[ConversationService.ingest] Starting ingestion: persona_id={self.persona['id']}, conversation_id={message.chat_id}, text_length={len(message.text)}")
        try:
            # A bridge can deliver the same inbound message more than once
            # (event re-emit, reconnect/history replay). Re-ingesting would
            # store a duplicate row and queue a second reply, so the provider
            # message id is the idempotency key here.
            if message.provider != "local" and message.message_id:
                existing = await get_message_by_external_id(
                    message.provider,
                    str(message.message_id),
                    persona_id=self.persona["id"],
                    direction="user",
                )
                if existing is not None:
                    logger.info(
                        "[ConversationService.ingest] Duplicate inbound message ignored: persona_id=%s, source=%s, external_id=%s, message_id=%s",
                        self.persona["id"],
                        message.provider,
                        message.message_id,
                        existing.id,
                    )
                    return { "message_id": existing.id }

            logger.debug(f"[ConversationService.ingest] Loading mood state for persona_id={self.persona['id']}")
            state = await self._load_state()

            message_id = await save_message(
                self.persona["id"],
                message.chat_id,
                "user",
                message.text,
                "delivered",
                source=message.provider,
                external_id=message.message_id,
                sender_id=message.sender_id,
                sender_name=message.sender_name,
                reply_to_message_id=message.reply_to_message_id,
                reply_to_text=message.reply_to_text,
            )

            logger.debug(f"[ConversationService.ingest] User message saved: message_id={message_id}")

            contact_id = message.sender_id or message.chat_id
            is_unknown = await self._contact_is_unknown(message.chat_id, message.sender_id)
            pester_level = await self._record_and_assess_contact(contact_id, message.chat_id, is_unknown)

            pester_event = mood_event(pester_level)
            if pester_event is not None:
                event = pester_event
                logger.info(f"[ConversationService.ingest] Pestering detected: level={pester_level.value}, event={event}")
            else:
                logger.debug(f"[ConversationService.ingest] Classifying event")
                event = await self._classify_event(message.text)

            logger.debug(f"[ConversationService.ingest] Event classified as: {event}")

            state = self.mood.update(state, event)
            logger.debug(f"[ConversationService.ingest] Mood state updated: {state.values}")

            self.states[message.chat_id] = state

            await self._save_state(state)
            logger.debug(f"[ConversationService.ingest] Mood state saved for persona_id={self.persona['id']}")

            presence = await self.get_presence()
            logger.debug(f"[ConversationService.ingest] Presence retrieved: online={presence.get('online') if presence else 'unknown'}")

            # A pestering contact may prompt the persona to confide in someone
            # it trusts, independently of whether it answers the sender.
            await self._maybe_schedule_confide(
                offender_id=contact_id,
                level=pester_level,
                sender_name=message.sender_name,
            )

            # Check contact existence
            if is_unknown and not settings.reply_unknown_contacts:
                logger.info(f"[ConversationService.ingest] Unknown contact ({contact_id}) and reply_unknown_contacts is disabled. Skipping reply.")
                # Still remember it: memory/trust processing is how a stranger
                # can eventually become a known contact.
                await self.short_memory.schedule(
                    "memory_pending",
                    self._memory_task_payload(message, message_id),
                    time(),
                )
                return { "message_id": message_id }

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
                # The message is still worth remembering even if the persona
                # chooses not to reply right now.
                await self.short_memory.schedule(
                    "memory_pending",
                    self._memory_task_payload(message, message_id),
                    time(),
                )
                return { "message_id": message_id }

            # The persona is answering this conversation now, so any standing
            # promise to get back to it is already being kept. Cancel it so it
            # cannot fire later as a duplicate.
            await self._cancel_commitments(message.chat_id)

            if decision is Decision.LATE_REPLY:
                due = time() + self._delay_seconds(state, message.text)
                logger.info(f"[ConversationService.ingest] Decision: LATE_REPLY, scheduling for {due - time():.1f} seconds from now")

                await self._schedule_reply(self._reply_task_payload(message, message_id), due)
                logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task: conversation_id={message.chat_id}")

                return { "message_id": message_id }

            logger.info(f"[ConversationService.ingest] Decision: REPLY_NOW, scheduling immediate reply")
            await self._schedule_reply(self._reply_task_payload(message, message_id), time())
            logger.debug(f"[ConversationService.ingest] Scheduled reply_pending task immediately: conversation_id={message.chat_id}")

            return { "message_id": message_id }
        except Exception as e:
            logger.error(f"[ConversationService.ingest] Error during ingest: {e}", exc_info=True)
            raise

    async def _record_and_assess_contact(
        self,
        contact_id: str,
        conversation_id: str,
        is_unknown: bool,
    ) -> PesterLevel:
        """Record the inbound message and return how much it is pestering.

        Runs only when the persona opted into ``social_awareness``; otherwise
        ingestion is unchanged.
        """
        cfg = self.persona.get("social_awareness") or {}
        if not cfg.get("enabled"):
            return PesterLevel.NEUTRAL

        persona_id = self.persona["id"]
        await self.short_memory.record_contact_message(persona_id, contact_id)
        activity = await self.short_memory.get_contact_activity(persona_id, contact_id)
        unanswered = await count_unanswered_user_messages(persona_id, conversation_id)

        level = evaluate_pestering(
            activity,
            unanswered,
            is_unknown=is_unknown,
            thresholds=cfg.get("thresholds"),
        )

        if level is not PesterLevel.NEUTRAL:
            await self._bump_annoyance(contact_id, level, cfg)
            logger.info(
                "[ConversationService.ingest] Pester level=%s for contact_id=%s (activity=%s, unanswered=%s)",
                level.value, contact_id, activity, unanswered,
            )

        return level

    async def _bump_annoyance(self, contact_id: str, level: PesterLevel, cfg: dict) -> None:
        """Decay then raise a contact's durable annoyance score."""
        contact = await get_contact(self.persona["id"], contact_id)
        if contact is None:
            # The memory pipeline creates the row after ingest; the next
            # message will carry the score.
            return

        updated_at = contact.get("updated_at")
        hours = 0.0
        if isinstance(updated_at, datetime):
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            hours = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600)

        rate = float(cfg.get("annoyance_decay_per_hour", 0.08))
        decayed = decay_annoyance(float(contact.get("annoyance", 0.0)), hours, rate)
        await update_contact_annoyance(
            self.persona["id"],
            contact_id,
            decayed + annoyance_increment(level),
        )

    async def _maybe_schedule_confide(
        self,
        offender_id: str,
        level: PesterLevel,
        sender_name: str | None = None,
    ) -> bool:
        """Queue one rate-limited vent about a pestering contact to a confidant.

        The persona must be online and inside an active window, the confidant
        must be a vetted high-trust contact, and per-pair/global/daily limits
        must all pass. The message itself is generated later by the worker.
        """
        cfg = self.persona.get("social_awareness") or {}
        if not cfg.get("enabled") or not is_at_least(level, PesterLevel.ANNOYED):
            return False

        persona_id = self.persona["id"]
        contacts = await list_contacts(persona_id)
        confidant = select_confidant(
            contacts,
            min_trust=float(cfg.get("min_confidant_trust", 0.72)),
            exclude_id=offender_id,
        )
        if confidant is None:
            return False

        presence = await self.get_presence()
        if not presence.get("online") or presence.get("busy"):
            return False

        windows = cfg.get("time_windows")
        if windows:
            local_hour = self._local_now().hour
            if not any(
                len(window) == 2 and int(window[0]) <= local_hour <= int(window[1])
                for window in windows
            ):
                return False

        confidant_id = str(confidant["contact_id"])
        pair_cooldown = int(float(cfg.get("confide_cooldown_minutes", 720)) * 60)
        if not await self.short_memory.claim_confide(persona_id, offender_id, confidant_id, pair_cooldown):
            logger.debug("[ConversationService._maybe_schedule_confide] Pair cooldown active; skipping")
            return False

        global_cooldown = int(float(cfg.get("confide_global_cooldown_minutes", 240)) * 60)
        if not await self.short_memory.claim_confide_global(persona_id, global_cooldown):
            await self.short_memory.release_confide(persona_id, offender_id, confidant_id)
            return False

        daily_key = f"persona:confide:count:{persona_id}:{self._local_now().date().isoformat()}"
        if not await self.short_memory.reserve_daily_follow_up(daily_key, int(cfg.get("daily_confide_budget", 2))):
            await self.short_memory.release_confide(persona_id, offender_id, confidant_id)
            return False

        confidant_conversation = await find_conversation_by_sender(persona_id, confidant_id) or confidant_id
        last_message = await get_last_user_message(persona_id, confidant_conversation)

        payload = {
            "conversation_id": confidant_conversation,
            "persona_id": persona_id,
            "trigger": "venting",
            "offender_id": offender_id,
            "pester_level": level.value,
            "confide_context": self._confide_context(level, sender_name, cfg),
        }
        if last_message:
            if last_message.get("source"):
                payload["source"] = last_message["source"]
            if last_message.get("sender_id"):
                payload["sender_id"] = last_message["sender_id"]
            if last_message.get("sender_name"):
                payload["sender_name"] = last_message["sender_name"]

        delay = float(cfg.get("confide_delay_seconds", 90))
        await self.short_memory.schedule("confide", payload, time() + delay)
        logger.info(
            "[ConversationService._maybe_schedule_confide] Scheduled venting about offender=%s to confidant=%s (level=%s)",
            offender_id, confidant_id, level.value,
        )
        return True

    @staticmethod
    def _confide_context(level: PesterLevel, sender_name: str | None, cfg: dict) -> str:
        share = bool(cfg.get("share_contact_identity", False))
        who = f"someone saved as {sender_name}" if (share and sender_name) else "an unsaved contact"
        return (
            f"You decided, entirely on your own, to confide in this trusted contact. "
            f"{who} keeps messaging you and it is getting on your nerves "
            f"(intensity: {level.value}). Talk about it the way you naturally would "
            f"with this person: short, real, a little vulnerable. Do not expose "
            f"private details such as phone numbers, usernames, or screenshots, and "
            f"do not turn it into a formal report."
        )

    async def _schedule_reply(self, payload: dict, due_at: float) -> bool:
        """Queue a reply for an inbound message at most once.

        ``ingest`` and ``process_unread_messages`` can both try to answer the
        same stored message; the per-message claim makes the first one win so
        the persona never sends two replies for one message.
        """
        user_message_id = payload.get("user_message_id")
        if user_message_id is not None and not await self.short_memory.claim_reply(
            self.persona["id"], int(user_message_id)
        ):
            logger.info(
                "[ConversationService._schedule_reply] Reply already queued for message_id=%s; skipping duplicate",
                user_message_id,
            )
            return False

        await self.short_memory.schedule("reply_pending", payload, due_at)
        return True

    async def schedule_commitment(
        self,
        conversation_id: str,
        commitment: Commitment,
        *,
        source: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a promise the persona just made and resolve it to an instant.

        The named window is turned into a concrete local time, biased toward
        the hours this persona is actually online, then stored durably. A new
        promise supersedes any standing one for the same conversation.
        """
        persona_id = self.persona["id"]
        try:
            state = self.current_state or await self._load_state()
            now_local = self._local_now()
            schedule = resolve_schedule(
                commitment.window,
                now_local=now_local,
                online_probability=lambda hour: self.behavior.online_probability(hour, state),
                rng=random.Random(),
            )
        except Exception as exc:
            logger.warning(
                "[ConversationService.schedule_commitment] Could not resolve window %r: %s",
                commitment.window, exc,
            )
            return None

        await self._cancel_commitments(conversation_id)

        try:
            record = await save_commitment(
                persona_id,
                conversation_id,
                kind=commitment.kind.value,
                window=commitment.window,
                topic=(commitment.topic or None),
                due_at=schedule.due_at.astimezone(timezone.utc),
                window_end=schedule.window_end.astimezone(timezone.utc),
                source=source,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        except Exception:
            logger.exception("[ConversationService.schedule_commitment] Failed to persist commitment")
            return None

        logger.info(
            "[ConversationService.schedule_commitment] Commitment #%s scheduled: conversation_id=%s window=%s due_at=%s",
            record["id"], conversation_id, commitment.window, schedule.due_at.isoformat(),
        )
        return record

    async def _cancel_commitments(self, conversation_id: str) -> int:
        try:
            return await cancel_open_commitments(self.persona["id"], conversation_id)
        except Exception:
            logger.exception(
                "[ConversationService._cancel_commitments] Failed for conversation_id=%s",
                conversation_id,
            )
            return 0

    async def process_due_commitments(self, limit: int = 10) -> int:
        """Claim due promises and deliver, defer, or expire each one.

        Runs from the life cycle rather than a fixed cron so timing follows the
        persona's own rhythm. Delivery reuses the normal reply pipeline via a
        ``commitment_reply`` task; here we only gate on presence and decide
        whether the moment is still right.
        """
        persona_id = self.persona["id"]
        now_utc = datetime.now(timezone.utc)

        # A worker that died mid-delivery leaves a row in ``sending``; requeue
        # it so a promise is not silently lost (at-least-once).
        try:
            await recover_stale_commitments(persona_id, now_utc - timedelta(seconds=120))
        except Exception:
            logger.exception("[ConversationService.process_due_commitments] Stale recovery failed")

        try:
            due = await claim_due_commitments(persona_id, now_utc, limit=limit)
        except Exception:
            logger.exception("[ConversationService.process_due_commitments] Claim failed")
            return 0

        if not due:
            return 0

        presence = await self.get_presence()
        available = bool(presence.get("online")) and not bool(presence.get("busy"))
        local_now = self._local_now()
        scheduled = 0

        for record in due:
            if available:
                await self.short_memory.schedule(
                    "commitment_reply",
                    self._commitment_task_payload(record),
                    time(),
                )
                scheduled += 1
                logger.info(
                    "[ConversationService.process_due_commitments] Queued follow-up #%s for conversation_id=%s",
                    record["id"], record["conversation_id"],
                )
                continue

            # Offline or busy: keep the promise inside its window if there is
            # still time, otherwise let it expire rather than texting at 4am.
            if local_now < record["window_end"] and record["attempts"] < 3:
                next_due = defer_within_window(local_now, record["window_end"], rng=random.Random())
                await reschedule_commitment(record["id"], next_due.astimezone(timezone.utc))
                logger.info(
                    "[ConversationService.process_due_commitments] Deferred follow-up #%s to %s",
                    record["id"], next_due.isoformat(),
                )
            else:
                await settle_commitment_db(record["id"], "expired")
                logger.info(
                    "[ConversationService.process_due_commitments] Expired follow-up #%s (window passed)",
                    record["id"],
                )

        return scheduled

    async def settle_commitment(self, commitment_id: int, delivered: bool) -> None:
        """Close out a delivered promise, or requeue/expire a failed delivery."""
        try:
            if delivered:
                await settle_commitment_db(commitment_id, "fulfilled")
                return

            record = await get_commitment(commitment_id)
            if record is None:
                return

            local_now = self._local_now()
            if local_now < record["window_end"]:
                next_due = defer_within_window(local_now, record["window_end"], rng=random.Random())
                await reschedule_commitment(commitment_id, next_due.astimezone(timezone.utc))
                logger.info(
                    "[ConversationService.settle_commitment] Requeued #%s after failed delivery to %s",
                    commitment_id, next_due.isoformat(),
                )
            else:
                await settle_commitment_db(commitment_id, "expired")
                logger.info(
                    "[ConversationService.settle_commitment] Expired #%s after failed delivery",
                    commitment_id,
                )
        except Exception:
            logger.exception(
                "[ConversationService.settle_commitment] Failed for commitment_id=%s",
                commitment_id,
            )

    @staticmethod
    def _commitment_task_payload(record: dict[str, Any]) -> dict[str, Any]:
        window = str(record.get("window", "")).replace("_", " ").strip()
        topic = (record.get("topic") or "").strip()
        context = (
            f"You promised this person you would reach out ({window or 'later'}). "
            "This is that moment. Follow through naturally, keeping it short and in "
            "your own voice. Do not mention scheduling, reminders, or that this was "
            "queued; do not apologize for the delay unless it is genuinely late."
        )
        if topic:
            context += f" What it was about: {topic}."

        return {
            "conversation_id": record["conversation_id"],
            "persona_id": record["persona_id"],
            "trigger": "follow_up",
            "commitment_id": record["id"],
            "commitment_context": context,
            "source": record.get("source"),
            "sender_id": record.get("sender_id"),
            "sender_name": record.get("sender_name"),
        }

    @staticmethod
    def _reply_task_payload(message: SocialMessage, message_id: int) -> dict:
        """Reply task routing info ONLY.

        Deliberately excludes the message text: the reply generator must read
        what the user said from conversation history. Embedding the text here
        caused echoed replies — on a re-scheduled task the worker reused the
        stale text (even the user's own words) as the outgoing reply.
        """
        return {
            "conversation_id": message.chat_id,
            "persona_id": message.session,
            "user_message_id": message_id,
            "source": message.provider,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "reply_to_message_id": message.reply_to_message_id,
            "reply_to_text": message.reply_to_text,
            "reply_to_external_id": message.message_id,
        }

    @staticmethod
    def _memory_task_payload(message: SocialMessage, message_id: int) -> dict:
        return {
            "conversation_id": message.chat_id,
            "persona_id": message.session,
            "user_message_id": message_id,
            "source": message.provider,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
        }

    @staticmethod
    def _memory_task_payload_from_action(action: dict, persona_id: str) -> dict:
        return {
            "conversation_id": action["conversation_id"],
            "persona_id": persona_id,
            "user_message_id": action["message_id"],
            "source": action.get("source"),
            "sender_id": action.get("sender_id"),
            "sender_name": action.get("sender_name"),
        }

    async def remember_message(
        self,
        conversation_id: str,
        text: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        user_message_id: int | None = None,
        source: str | None = None,
    ) -> None:
        """Run only the memory agent over an inbound message.

        Ensures every message gets a chance to be stored even when the persona
        never replies (NO_REPLY, offline, unknown contact). The graph extracts
        durable facts from history + this message and updates contact trust.
        Deduplicated per message so ingest + unread-sweep scheduling cannot
        process the same message twice.
        """
        flag_key = None
        if user_message_id is not None:
            flag_key = f"persona:memory:processed:{self.persona['id']}:{user_message_id}"
            try:
                if await self.short_memory.get_json(flag_key):
                    logger.debug(
                        "[ConversationService.remember_message] Message already memory-processed, skipping: message_id=%s",
                        user_message_id,
                    )
                    return
            except Exception:
                flag_key = None

        await self.agent.remember_message(
            conversation_id=conversation_id,
            text=text,
            sender_id=sender_id,
            sender_name=sender_name,
            source=source,
        )

        if flag_key is not None:
            try:
                await self.short_memory.set_json(
                    flag_key,
                    {"processed_at": datetime.now(timezone.utc).isoformat()},
                    ttl=7 * 24 * 60 * 60,
                )
            except Exception:
                pass

    async def generate_reply(
        self,
        conversation_id: str,
        initiative: str | None = None,
        source: str = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        text: str | None = None,
        reply_to_message_id: str | None = None,
        reply_to_text: str | None = None,
        confide_context: str | None = None,
        commitment_context: str | None = None,
    ) -> AgentResponse:
        logger.info(f"[ConversationService.generate_reply] Generating reply for conversation_id={conversation_id}, persona_id={self.persona['id']}")
        try:
            logger.debug(f"[ConversationService.generate_reply] Marking conversation messages as seen before typing")
            await self.mark_conversation_seen(conversation_id, source=source)

            # Route typing indicator ONLY to local UI if source is local
            if source is None or source == "local":
                logger.debug(f"[ConversationService.generate_reply] Publishing typing indicator to UI")
                await self.short_memory.publish_typing(self.persona["id"], conversation_id, True)

            try:
                logger.debug(f"[ConversationService.generate_reply] Invoking persona LangGraph")
                start = time_module.time()
                state = self.states.get(conversation_id) or await self._load_state()
                self.states[conversation_id] = state
                response = await self.agent.generate_reply(
                    conversation_id,
                    state.values,
                    initiative=initiative,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    source=source,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                    reply_to_text=reply_to_text,
                    confide_context=confide_context,
                    commitment_context=commitment_context,
                )
                elapsed = time_module.time() - start

                logger.info(f"[ConversationService.generate_reply] AI response generated in {elapsed:.2f}s: conversation_id={conversation_id}, type={response.type}")
                return response
            finally:
                if source is None or source == "local":
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
                
                # Check contact existence
                contact_id = message.get("sender_id") or message["conversation_id"]
                if await self._contact_is_unknown(message["conversation_id"], message.get("sender_id")) and not settings.reply_unknown_contacts:
                    logger.info(f"[ConversationService.process_unread_messages] Unknown contact ({contact_id}) and reply_unknown_contacts is disabled. Skipping reply.")
                    await self.short_memory.schedule(
                        "memory_pending",
                        {
                            "conversation_id": message["conversation_id"],
                            "persona_id": self.persona["id"],
                            "user_message_id": message["id"],
                            "source": message.get("source"),
                            "sender_id": message.get("sender_id"),
                            "sender_name": message.get("sender_name"),
                        },
                        time(),
                    )
                    continue

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
                    "source": message["source"],
                    "sender_id": message["sender_id"],
                    "sender_name": message["sender_name"],
                    "external_id": message.get("external_id"),
                    "reply_to_message_id": message.get("reply_to_message_id"),
                    "reply_to_text": message.get("reply_to_text"),
                })

            await self._save_state(state)
            logger.debug(f"[ConversationService.process_unread_messages] State saved after processing all messages")

            logger.debug(f"[ConversationService.process_unread_messages] Processing {len(actions)} actions")
            for action in actions:
                if action["decision"] != Decision.NO_REPLY.value:
                    # Answering this conversation now supersedes any standing promise.
                    await self._cancel_commitments(action["conversation_id"])
                if action["decision"] == Decision.LATE_REPLY.value:
                    delay = self._delay_seconds(state, action["content"])
                    logger.info(f"[ConversationService.process_unread_messages] Scheduling LATE_REPLY: conversation_id={action['conversation_id']}, delay_seconds={delay:.1f}")

                    await self._schedule_reply(
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                            "user_message_id": action["message_id"],
                            "source": action["source"],
                            "sender_id": action["sender_id"],
                            "sender_name": action["sender_name"],
                            "reply_to_message_id": action.get("reply_to_message_id"),
                            "reply_to_text": action.get("reply_to_text"),
                            "reply_to_external_id": action.get("external_id"),
                        },
                        time() + delay,
                    )
                elif action["decision"] == Decision.REPLY_NOW.value:
                    logger.info(f"[ConversationService.process_unread_messages] Scheduling immediate reply: conversation_id={action['conversation_id']}")
                    await self._schedule_reply(
                        {
                            "conversation_id": action["conversation_id"],
                            "persona_id": self.persona["id"],
                            "user_message_id": action["message_id"],
                            "source": action["source"],
                            "sender_id": action["sender_id"],
                            "sender_name": action["sender_name"],
                            "reply_to_message_id": action.get("reply_to_message_id"),
                            "reply_to_text": action.get("reply_to_text"),
                            "reply_to_external_id": action.get("external_id"),
                        },
                        time(),
                    )
                else:
                    logger.debug(f"[ConversationService.process_unread_messages] No reply needed: decision={action['decision']}; scheduling memory-only task")
                    await self.short_memory.schedule(
                        "memory_pending",
                        self._memory_task_payload_from_action(action, self.persona["id"]),
                        time(),
                    )


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

        if not settings.reply_unknown_contacts:
            candidates = [
                candidate
                for candidate in candidates
                if not await self._contact_is_unknown(candidate["conversation_id"])
            ]
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

        # Carry the conversation's transport metadata so the worker can route
        # the autonomous message back through the same bridge (WhatsApp,
        # Instagram, ...) instead of only the local inbox.
        last_user_message = await get_last_user_message(self.persona["id"], conversation_id)

        payload = {
            "conversation_id": conversation_id,
            "persona_id": self.persona["id"],
            "trigger": trigger["type"],
        }
        if last_user_message:
            if last_user_message.get("source"):
                payload["source"] = last_user_message["source"]
            if last_user_message.get("sender_id"):
                payload["sender_id"] = last_user_message["sender_id"]
            if last_user_message.get("sender_name"):
                payload["sender_name"] = last_user_message["sender_name"]

        await self.short_memory.schedule(
            "follow_up",
            payload,
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
