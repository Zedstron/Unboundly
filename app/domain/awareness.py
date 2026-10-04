"""Social awareness: detect pestering contacts and pick a confidant.

This is the deterministic half of the "confide in someone you trust" behavior.
It answers two questions without any LLM involvement:

1. Has an inbound contact crossed from ordinary chatty into pestering? The
   answer comes from rolling message counts (Redis) plus the unanswered streak
   (SQLite).
2. Who, if anyone, is trusted enough to be told about it?

The persona only ever *decides* here. The actual venting message is generated
and routed by the normal reply pipeline, so tone and wording stay in the graph.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable


class PesterLevel(StrEnum):
    NEUTRAL = "neutral"
    NOTICE = "notice"
    ANNOYED = "annoyed"
    HARASSED = "harassed"


_ORDER: dict[PesterLevel, int] = {
    PesterLevel.NEUTRAL: 0,
    PesterLevel.NOTICE: 1,
    PesterLevel.ANNOYED: 2,
    PesterLevel.HARASSED: 3,
}


# Rolling-window thresholds. A window key maps to the message count at which
# that level is reached. Unknown contacts use these strict defaults; a persona
# config may override any of them under ``social_awareness.thresholds``.
DEFAULT_THRESHOLDS: dict[str, int] = {
    "notice_burst_2m": 3,
    "notice_sustained_10m": 5,
    "notice_unanswered": 4,
    "annoyed_sustained_10m": 8,
    "annoyed_hourly": 12,
    "annoyed_unanswered": 8,
    "harassed_hourly": 15,
    "harassed_daily": 30,
    "harassed_unanswered": 15,
}

# Mood delta applied the first time each pester level is observed. Deliberately
# separate from the annoyance score: mood is the persona's transient feeling,
# annoyance is a durable memory of the contact.
ANNOYANCE_INCREMENT: dict[PesterLevel, float] = {
    PesterLevel.NEUTRAL: 0.0,
    PesterLevel.NOTICE: 0.1,
    PesterLevel.ANNOYED: 0.25,
    PesterLevel.HARASSED: 0.4,
}


def mood_event(level: PesterLevel) -> str | None:
    """Mood event name for a pester level (None when the contact is normal)."""
    if level is PesterLevel.HARASSED:
        return "boundary_push"
    if level in (PesterLevel.NOTICE, PesterLevel.ANNOYED):
        return "pestering"
    return None


def evaluate_pestering(
    activity: dict[str, int],
    unanswered: int,
    *,
    is_unknown: bool = True,
    thresholds: dict[str, Any] | None = None,
) -> PesterLevel:
    """Classify how much a contact is pestering the persona right now.

    ``activity`` holds rolling message counts keyed by ``burst_2m``,
    ``sustained_10m``, ``hourly`` and ``daily``. ``unanswered`` is the number of
    inbound messages received since the persona last replied in the thread.

    Unknown contacts are held to the strict thresholds. Known contacts are
    treated more leniently (a friend who chats a lot is not harassment), so
    their thresholds are relaxed by a factor.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    burst = int(activity.get("burst_2m", 0))
    sustained = int(activity.get("sustained_10m", 0))
    hourly = int(activity.get("hourly", 0))
    daily = int(activity.get("daily", 0))
    unanswered = max(0, int(unanswered))

    if is_unknown:
        unknown_factor = 1.0
    else:
        unknown_factor = 2.0

    def reached(key: str) -> bool:
        limit = limits.get(key, 0)
        if limit <= 0:
            return False
        return int(round(limit * unknown_factor))

    if (
        hourly >= reached("harassed_hourly")
        or daily >= reached("harassed_daily")
        or unanswered >= reached("harassed_unanswered")
    ):
        return PesterLevel.HARASSED

    if (
        sustained >= reached("annoyed_sustained_10m")
        or hourly >= reached("annoyed_hourly")
        or unanswered >= reached("annoyed_unanswered")
    ):
        return PesterLevel.ANNOYED

    if (
        burst >= reached("notice_burst_2m")
        or sustained >= reached("notice_sustained_10m")
        or unanswered >= reached("notice_unanswered")
    ):
        return PesterLevel.NOTICE

    return PesterLevel.NEUTRAL


def is_at_least(level: PesterLevel, minimum: PesterLevel) -> bool:
    return _ORDER[level] >= _ORDER[minimum]


def annoyance_increment(level: PesterLevel) -> float:
    return ANNOYANCE_INCREMENT.get(level, 0.0)


def decay_annoyance(value: float, hours: float, rate: float = 0.08) -> float:
    """Annoyance fades toward zero over time, mirroring mood decay."""
    value = max(0.0, min(1.0, float(value)))
    hours = max(0.0, float(hours))
    return max(0.0, value * (1.0 - min(rate * hours, 1.0)))


def select_confidant(
    contacts: Iterable[dict[str, Any]],
    *,
    min_trust: float,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    """Highest-trust vetted contact eligible to be confided in.

    Contacts must be explicitly known (``is_unknown`` false) and meet the trust
    floor; the offender is never a confidant candidate.
    """
    eligible = [
        contact
        for contact in contacts
        if not contact.get("is_unknown")
        and float(contact.get("trust", 0.0)) >= float(min_trust)
        and (exclude_id is None or str(contact.get("contact_id")) != str(exclude_id))
    ]

    if not eligible:
        return None

    return max(eligible, key=lambda contact: float(contact.get("trust", 0.0)))
