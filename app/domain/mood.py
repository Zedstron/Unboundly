from typing import Any
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class MoodState:
    values: dict[str, float]
    updated_at: datetime

class MoodEngine:
    def __init__(self, persona: dict[str, Any]) -> None:
        self.config = persona["mood"]
        self.persona = persona

    def update(self, state: MoodState, event: str) -> MoodState:
        now = datetime.now(timezone.utc)

        hours = max((now - state.updated_at).total_seconds() / 3600, 0)
        decay = float(self.config.get("decay_per_hour", 0.08))

        updated = {}
        baseline = self.config["baseline"]

        for key, current in state.values.items():
            updated[key] = current + (baseline.get(key, 0.0) - current) * min(decay * hours, 1.0)

        for key, delta in self.config.get("event_weights", {}).get(event, {}).items():
            updated[key] = max(-1.0, min(1.0, updated.get(key, 0.0) + delta))

        for key, delta in self._cycle_modifier(now).items():
            if key in updated:
                updated[key] = max(-1.0, min(1.0, updated[key] + delta))

        return MoodState(updated, now)

    def _cycle_modifier(self, now: datetime) -> dict[str, float]:
        cfg = self.persona.get("biological_cycle", {})

        if not cfg.get("enabled") or self.persona.get("profile", {}).get("gender") != "female":
            return {}

        day = now.day

        phase = "menstrual" if day <= 5 else "follicular" if day <= 13 else "ovulatory" if day <= 16 else "luteal"
        return cfg.get("modifiers", {}).get(phase, {})
