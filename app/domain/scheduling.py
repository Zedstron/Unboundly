"""Resolve a persona's spoken time promise into a concrete instant.

The persona says things like "I'll text you tonight". The generation model
emits that as a structured :class:`~app.domain.models.Commitment` (a named
window, never a raw timestamp). This module turns the window into a concrete
local datetime, biased toward the hours the persona is actually online, so the
follow-up lands when it plausibly would.

Everything here works in the persona's local timezone; callers convert to a
POSIX timestamp for storage/routing. Keeping the decision in the domain (rather
than in a cron or a generic job queue) is what lets different personas have
different rhythms and timezones.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.domain.models import CommitmentWindow

# Fixed local-hour windows, inclusive.
_FIXED_WINDOWS: dict[str, tuple[int, int]] = {
    "this_evening": (18, 20),
    "tonight": (21, 22),
    "tomorrow_morning": (8, 10),
    "tomorrow_evening": (18, 20),
}

# Relative windows expressed as a minute range from now.
_RELATIVE_WINDOWS: dict[str, tuple[int, int]] = {
    "in_a_bit": (10, 40),
    "later_today": (60, 180),
}

# "this_week" samples a future day and an hour inside the persona's waking band.
_WEEK_DAY_OFFSETS: tuple[int, int] = (1, 6)
_WEEK_HOURS: tuple[int, int] = (9, 21)

# A promise must resolve to a moment later than the message that made it.
_MIN_LEAD_SECONDS = 60

# Bounded retry when a commitment comes due while the persona is offline/busy.
_DEFER_MIN_MINUTES = 5
_DEFER_MAX_MINUTES = 25


@dataclass(frozen=True)
class ResolvedSchedule:
    """A resolved promise: the exact instant plus the window it must land in.

    ``window_end`` is the deadline the worker must keep trying until; once it
    passes without delivery the commitment expires rather than firing at a
    nonsensical hour.
    """

    due_at: datetime
    window_start: datetime
    window_end: datetime


def resolve_schedule(
    window: CommitmentWindow,
    *,
    now_local: datetime,
    online_probability: Callable[[int], float] | None = None,
    rng: random.Random | None = None,
) -> ResolvedSchedule:
    """Turn a window into a concrete local datetime, weighted by availability.

    ``online_probability`` maps a local hour to the chance the persona is
    online then; when supplied, hours the persona rarely uses are de-prioritized
    so a night owl does not promise a message at 7am. ``rng`` makes the choice
    reproducible for tests.
    """
    rng = rng or random.Random()
    now_local = now_local.replace(second=0, microsecond=0)

    if window in _RELATIVE_WINDOWS:
        return _resolve_relative(window, now_local=now_local, rng=rng)

    if window == "this_week":
        return _resolve_this_week(
            now_local=now_local, online_probability=online_probability, rng=rng
        )

    return _resolve_fixed(
        window,
        now_local=now_local,
        online_probability=online_probability,
        rng=rng,
    )


def defer_within_window(
    now_local: datetime,
    window_end: datetime,
    *,
    rng: random.Random | None = None,
) -> datetime:
    """Pick the next retry moment, clamped inside the remaining window."""
    rng = rng or random.Random()
    minutes = rng.randint(_DEFER_MIN_MINUTES, _DEFER_MAX_MINUTES)
    candidate = now_local + timedelta(minutes=minutes)
    latest = window_end - timedelta(minutes=1)
    if candidate > latest:
        candidate = latest
    if candidate <= now_local:
        candidate = now_local + timedelta(seconds=30)
    return candidate.replace(second=0, microsecond=0)


def _resolve_relative(
    window: str,
    *,
    now_local: datetime,
    rng: random.Random,
) -> ResolvedSchedule:
    low, high = _RELATIVE_WINDOWS[window]
    due = now_local + timedelta(minutes=rng.randint(low, high))

    # "later today" must not spill into the small hours; if it crosses
    # midnight, move the promise to the persona's next morning instead of
    # texting at 2am.
    if window == "later_today" and due.date() != now_local.date():
        due = due.replace(hour=rng.randint(8, 10), minute=rng.randint(0, 59))

    window_start = now_local + timedelta(minutes=low)
    window_end = max(due + timedelta(minutes=30), now_local + timedelta(minutes=high))
    return ResolvedSchedule(due_at=_floor(due), window_start=_floor(window_start), window_end=_floor(window_end))


def _resolve_this_week(
    *,
    now_local: datetime,
    online_probability: Callable[[int], float] | None,
    rng: random.Random,
) -> ResolvedSchedule:
    day = now_local + timedelta(days=rng.randint(*_WEEK_DAY_OFFSETS))
    hour = _weighted_hour(_WEEK_HOURS[0], _WEEK_HOURS[1], online_probability, rng)
    due = day.replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)
    return ResolvedSchedule(
        due_at=due,
        window_start=day.replace(hour=_WEEK_HOURS[0], minute=0, second=0, microsecond=0),
        window_end=day.replace(hour=_WEEK_HOURS[1], minute=59, second=59, microsecond=0),
    )


def _resolve_fixed(
    window: str,
    *,
    now_local: datetime,
    online_probability: Callable[[int], float] | None,
    rng: random.Random,
) -> ResolvedSchedule:
    start_hour, end_hour = _FIXED_WINDOWS[window]
    day = now_local

    # "tomorrow_*" always means the next day; "this_evening"/"tonight" roll
    # forward only once their window has passed.
    if window in ("tomorrow_morning", "tomorrow_evening"):
        day = day + timedelta(days=1)

    window_start = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    window_end = day.replace(hour=end_hour, minute=59, second=59, microsecond=0)

    if window not in ("tomorrow_morning", "tomorrow_evening") and window_end <= now_local + timedelta(seconds=_MIN_LEAD_SECONDS):
        day = day + timedelta(days=1)
        window_start = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        window_end = day.replace(hour=end_hour, minute=59, second=59, microsecond=0)

    if window_start <= now_local:
        # The window is already underway: use the part of it still ahead.
        window_start = now_local + timedelta(seconds=_MIN_LEAD_SECONDS)

    hour = _weighted_hour(window_start.hour, end_hour, online_probability, rng)
    due = day.replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)

    earliest = now_local + timedelta(seconds=_MIN_LEAD_SECONDS)
    if due < earliest:
        due = earliest
    if due > window_end:
        due = window_end

    return ResolvedSchedule(due_at=due, window_start=window_start, window_end=window_end)


def _weighted_hour(
    low: int,
    high: int,
    online_probability: Callable[[int], float] | None,
    rng: random.Random,
) -> int:
    hours = list(range(low, high + 1))
    if not hours:
        return low
    if len(hours) == 1 or online_probability is None:
        return rng.choice(hours)

    weights = [max(0.01, _safe_probability(online_probability(hour))) for hour in hours]
    return rng.choices(hours, weights=weights, k=1)[0]


def _safe_probability(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, value))


def _floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)
