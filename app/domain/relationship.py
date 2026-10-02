"""Relationship stages derived from the hidden trust score.

Trust itself stays the single source of truth (a float in [-1.0, 1.0] kept in
SQLite). A stage is a named, human-readable band on that scale so the persona
prompt can say "this is your boyfriend" instead of showing the model a bare
number, which LLMs handle poorly.

Stages are ordered; two contacts with the same stage are treated the same way
even if their raw trust differs slightly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class RelationshipStage(StrEnum):
    STRANGER = "stranger"
    ACQUAINTANCE = "acquaintance"
    FRIEND = "friend"
    CLOSE_FRIEND = "close_friend"
    BEST_FRIEND = "best_friend"
    CRUSH = "crush"
    FLIRTING = "flirting"
    IN_LOVE = "in_love"
    BOYFRIEND = "boyfriend"
    GIRLFRIEND = "girlfriend"
    PARTNER = "partner"
    ENGAGED = "engaged"
    FAMILY = "family"
    BLOCKED = "blocked"


# (stage, minimum inclusive trust). Ordered ascending by threshold.
# The first stage whose threshold is <= trust wins.
# Trust 0.0 — the default for a brand-new contact — is deliberately STRANGER.
_STAGE_LADDER: tuple[tuple[RelationshipStage, float], ...] = (
    (RelationshipStage.STRANGER, -1.00),
    (RelationshipStage.ACQUAINTANCE, 0.01),
    (RelationshipStage.FRIEND, 0.10),
    (RelationshipStage.CLOSE_FRIEND, 0.25),
    (RelationshipStage.BEST_FRIEND, 0.40),
    (RelationshipStage.CRUSH, 0.55),
    (RelationshipStage.FLIRTING, 0.65),
    (RelationshipStage.IN_LOVE, 0.72),
    (RelationshipStage.BOYFRIEND, 0.78),
    (RelationshipStage.GIRLFRIEND, 0.78),
    (RelationshipStage.PARTNER, 0.88),
    (RelationshipStage.ENGAGED, 0.95),
)

# Partner and above are romantic by default; the gender-aware branch picks
# boyfriend/girlfriend inside the 0.78-0.88 band.
_ROMANTIC_FALLBACK = RelationshipStage.PARTNER
_MALE_ROMANTIC = RelationshipStage.BOYFRIEND
_FEMALE_ROMANTIC = RelationshipStage.GIRLFRIEND

_BLOCKED_TRUST = -0.60

# Prompts read better in second person: what the persona feels toward this
# contact and how much effort/warmth the relationship justifies.
_STAGE_GUIDANCE: dict[RelationshipStage, str] = {
    RelationshipStage.STRANGER: (
        "a stranger. You do not know this person yet; you have no shared history. "
        "Be politely reserved and cautious, keep some distance, and do not overshare "
        "personal details or treat them like a friend."
    ),
    RelationshipStage.ACQUAINTANCE: (
        "an acquaintance: someone you have exchanged a few words with, nothing more. "
        "Friendly but guarded — light small talk is fine, deep personal topics are not."
    ),
    RelationshipStage.FRIEND: (
        "a friend. Casual and relaxed; jokes and honest opinions are okay, but this "
        "is not someone you tell everything to."
    ),
    RelationshipStage.CLOSE_FRIEND: (
        "a close friend. Comfortable and warm; you share more of your life with them "
        "than with most people and can be more open about feelings."
    ),
    RelationshipStage.BEST_FRIEND: (
        "one of your best friends. Complete comfort: teasing, inside-joke energy, "
        "real talk, and leaning on each other when it matters."
    ),
    RelationshipStage.CRUSH: (
        "someone you quietly have a crush on. You like them more than you admit; "
        "extra attention and a little nervous warmth sneak into your replies, "
        "but you are careful not to make it obvious."
    ),
    RelationshipStage.FLIRTING: (
        "someone you are openly flirting with. Playful teasing, compliments and "
        "mutual interest are out in the air, though nothing is official yet."
    ),
    RelationshipStage.IN_LOVE: (
        "someone you are in love with, and they know it. Affectionate, a little "
        "clingy sometimes, genuinely invested in their day and wellbeing."
    ),
    RelationshipStage.BOYFRIEND: (
        "your boyfriend. An established romantic relationship: affectionate, "
        "comfortable, occasionally possessive or needy, with the easy intimacy "
        "of a couple."
    ),
    RelationshipStage.GIRLFRIEND: (
        "your girlfriend. An established romantic relationship: affectionate, "
        "comfortable, occasionally possessive or needy, with the easy intimacy "
        "of a couple."
    ),
    RelationshipStage.PARTNER: (
        "your long-term partner. Deep commitment and shared history: you talk like "
        "a settled couple, use established pet names, and skip formalities entirely."
    ),
    RelationshipStage.ENGAGED: (
        "your fiancé/fiancée. Committed and future-facing: deep intimacy, shared "
        "plans, and the ease of someone about to marry you."
    ),
    RelationshipStage.FAMILY: (
        "family. Unconditional but familiar: casual, blunt where appropriate, "
        "with the shorthand and history only family has."
    ),
    RelationshipStage.BLOCKED: (
        "someone you are effectively done with — they badly damaged your trust. "
        "Cold, short, guarded replies at best; you owe them nothing."
    ),
}


def stage_for_trust(trust: float, persona_gender: str | None = None) -> RelationshipStage:
    """Map a trust value to its relationship stage.

    The romantic band 0.78-0.88 is gender-aware: "boyfriend" if the persona is
    male (their romantic partner is a boyfriend to them), "girlfriend" if
    female. Above 0.88 the generic partner/engaged stages apply. Above +0.99
    the persona is family-level close.
    """
    trust = max(-1.0, min(1.0, float(trust)))

    if trust <= _BLOCKED_TRUST:
        return RelationshipStage.BLOCKED

    if trust >= 0.99:
        return RelationshipStage.FAMILY

    if trust >= _STAGE_LADDER[-1][1]:  # >= 0.95
        return RelationshipStage.ENGAGED

    if trust >= 0.88:
        return _ROMANTIC_FALLBACK

    if trust >= 0.78:
        gender = (persona_gender or "").strip().lower()
        if gender in {"male", "m", "man", "boy"}:
            return _MALE_ROMANTIC
        if gender in {"female", "f", "woman", "girl"}:
            return _FEMALE_ROMANTIC
        return _ROMANTIC_FALLBACK

    for stage, threshold in reversed(_STAGE_LADDER):
        if trust >= threshold:
            return stage

    return RelationshipStage.STRANGER


def stage_description(stage: RelationshipStage) -> str:
    return _STAGE_GUIDANCE.get(stage, _STAGE_GUIDANCE[RelationshipStage.STRANGER])


def is_blocked(stage: RelationshipStage) -> bool:
    return stage is RelationshipStage.BLOCKED


def is_romantic(stage: RelationshipStage) -> bool:
    return stage in {
        RelationshipStage.CRUSH,
        RelationshipStage.FLIRTING,
        RelationshipStage.IN_LOVE,
        RelationshipStage.BOYFRIEND,
        RelationshipStage.GIRLFRIEND,
        RelationshipStage.PARTNER,
        RelationshipStage.ENGAGED,
    }


def describe_relationship(
    trust: float,
    name: str | None,
    persona_gender: str | None = None,
    is_unknown: bool = False,
) -> str:
    """Render the [CONTACT / RELATIONSHIP] block injected into the persona prompt."""
    stage = stage_for_trust(trust, persona_gender)

    if is_unknown:
        lines = [
            "Status: unknown contact (not saved yet)",
            "Stage: stranger",
            f"Internal trust: {trust:+.2f} (scale -1.0 to 1.0)",
            f"Who they are to you: {stage_description(stage)}",
        ]
        return "\n".join(lines)

    display_name = (name or "").strip() or "Unknown"
    lines = [
        f"Name: {display_name}",
        f"Status: known contact — {stage.value.replace('_', ' ')}",
        f"Internal trust: {trust:+.2f} (scale -1.0 to 1.0)",
        f"Who they are to you: {stage_description(stage)}",
    ]

    if is_blocked(stage):
        lines.append(
            f"This person is currently {RelationshipStage.BLOCKED.value} to you: "
            "keep replies minimal and cold, and do not warm up quickly."
        )

    return "\n".join(lines)

def relationship_payload(trust: float, persona_gender: str | None = None) -> dict[str, Any]:
    """Compact machine-readable form stored alongside contacts / used by the UI."""
    stage = stage_for_trust(trust, persona_gender)
    return {
        "stage": stage.value,
        "trust": max(-1.0, min(1.0, float(trust))),
        "romantic": is_romantic(stage),
        "blocked": is_blocked(stage),
        "description": stage_description(stage),
    }
