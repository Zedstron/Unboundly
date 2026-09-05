import math
import random
from dataclasses import dataclass
from typing import Any

from .models import Decision
from .mood import MoodState


@dataclass(frozen=True)
class BehaviorContext:
    hour: int
    idle_minutes: float
    unread: bool
    online: bool | None = None


class BehaviorEngine:
    """
    Decides whether the persona should:
        - not reply
        - reply later
        - reply now

    This engine intentionally does not manage message state such as
    delivered/seen/unseen. Those belong to the messaging layer.
    """

    def __init__(self, persona: dict[str, Any], rng: random.Random | None = None) -> None:
        self.p = persona
        self.rng = rng or random.Random()

        self.availability = self._load_availability()
        self.mood_config = self.p.get("mood", {})

    def decide(
        self,
        mood: MoodState,
        ctx: BehaviorContext,
    ) -> Decision:
        hour = self._normalize_hour(ctx.hour)
        idle_minutes = max(0.0, float(ctx.idle_minutes))

        # ---------------------------------------------------------
        # 1. Determine probability that the persona is available
        # ---------------------------------------------------------
        online_p = self._online_probability(hour, mood)

        # Persona may be online but busy.
        busy_p = self._clamp(
            float(self.availability.get("busy_probability", 0.0))
        )

        available_p = online_p * (1.0 - busy_p)

        # If the message is already unread, account for the
        # probability that the persona actually notices/reads it.
        if ctx.unread:
            read_p = self._clamp(
                float(
                    self.availability.get(
                        "read_probability_when_online",
                        0.86,
                    )
                )
            )
            available_p *= read_p

        # ---------------------------------------------------------
        # 2. Calculate willingness to reply
        # ---------------------------------------------------------
        reply_p = self._reply_probability(
            mood=mood,
            idle_minutes=idle_minutes,
        )

        # ---------------------------------------------------------
        # 3. One probability gate
        #
        # Using one final random draw makes the decision less noisy
        # ---------------------------------------------------------
        reply_probability = self._clamp(
            available_p * reply_p,
            0.0, 
            0.97,
        )

        if self.rng.random() >= reply_probability:
            return Decision.NO_REPLY

        # ---------------------------------------------------------
        # 4. Decide immediate vs delayed response
        # ---------------------------------------------------------
        late_probability = self._late_reply_probability(
            idle_minutes=idle_minutes,
            mood=mood,
        )

        if ctx.online is True:
            late_probability *= 0.35

        if self.rng.random() < late_probability:
            return Decision.LATE_REPLY

        return Decision.REPLY_NOW

    def online_probability(self, hour: int, mood: MoodState) -> float:
        """Return the persona's current chance of being online."""
        online = self._online_probability(self._normalize_hour(hour), mood)
        busy = self._clamp(float(self.availability.get("busy_probability", 0.0)))
        return self._clamp(online * (1.0 - busy))

    def _online_probability(self, hour: int, mood: MoodState) -> float:
        probabilities = self.availability[
            "online_probability_by_hour"
        ]

        base = self._clamp(float(probabilities[hour]))

        arousal = self._mood_value(mood, "arousal")
        fear = self._mood_value(mood, "fear")

        adjusted = (
            base
            + 0.08 * arousal
            - 0.12 * fear
        )

        return self._clamp(adjusted)

    def _reply_probability(self, mood: MoodState, idle_minutes: float) -> float:
        availability = self.availability

        reply_p = self._clamp(
            float(
                availability.get(
                    "reply_probability_when_seen",
                    0.78,
                )
            )
        )

        affection = self._mood_value(mood, "affection")
        curiosity = self._mood_value(mood, "curiosity")
        irritability = self._mood_value(mood, "irritability")
        fear = self._mood_value(mood, "fear")

        reply_p += 0.12 * affection
        reply_p += 0.08 * curiosity
        reply_p -= 0.22 * irritability
        reply_p -= 0.20 * fear

        # Very short idle means the persona may not have had enough
        # time to meaningfully process the previous interaction.
        if idle_minutes < 1.0:
            reply_p -= 0.10

        return self._clamp(reply_p, 0.03, 0.97)

    def _late_reply_probability(self, idle_minutes: float, mood: MoodState) -> float:
        """
        Produces a smooth transition from immediate to delayed replies.

        Instead of:
            if idle_minutes > 240:
                late

        we gradually increase the probability of a late reply.
        """

        # Base probability of replying later.
        probability = 0.10

        # Gradually increase delay tendency after 2 hours.
        if idle_minutes > 120:
            progress = min(
                (idle_minutes - 120.0) / 360.0,
                1.0,
            )
            probability += 0.30 * progress

        # Very long idle gaps strongly favor a delayed response.
        if idle_minutes > 480:
            probability += 0.15

        # High arousal can make responses more immediate.
        arousal = self._mood_value(mood, "arousal")
        probability -= 0.05 * arousal

        # High irritability can make the response slower/more hesitant.
        irritability = self._mood_value(mood, "irritability")
        probability += 0.08 * irritability

        return self._clamp(probability, 0.05, 0.75)


    def _load_availability(self) -> dict[str, Any]:
        availability = self.p.get("availability")

        if not isinstance(availability, dict):
            raise ValueError("Persona availability configuration is required.")

        hourly = availability.get("online_probability_by_hour")

        if not isinstance(hourly, list) or len(hourly) != 24:
            raise ValueError(
                "online_probability_by_hour must contain exactly 24 values."
            )

        return availability

    @staticmethod
    def _normalize_hour(hour: int) -> int:
        return int(hour) % 24

    @staticmethod
    def _mood_value(
        mood: MoodState,
        key: str,
    ) -> float:
        value = mood.values.get(key, 0.0)

        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0

        if not math.isfinite(value):
            return 0.0

        return max(-1.0, min(1.0, value))

    @staticmethod
    def _clamp(
        value: float,
        minimum: float = 0.0,
        maximum: float = 1.0,
    ) -> float:
        if not math.isfinite(value):
            return minimum

        return max(minimum, min(maximum, value))