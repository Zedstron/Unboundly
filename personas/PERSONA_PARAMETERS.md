# Persona Parameters Guide

This guide explains the fields in a persona JSON file such as `munazza.json`.

The application loads **only `.json` files** from this folder as personas. This Markdown file is safe to keep here and is not loaded as a persona.

## Quick principles

- Probabilities use `0.0` to `1.0`: `0` means never and `1` means always.
- Mood values use `-1.0` to `1.0`: negative is lower/less of that dimension; positive is higher/more.
- Hour arrays always contain **24 values**: index `0` is 12 AM and index `23` is 11 PM, in the persona's timezone.
- Avoid values of exactly `0` or `1` for normal human behaviour. Some uncertainty makes the persona feel less mechanical.
- The current persona timezone determines hourly presence, busyness, and self-follow-up timing.

---

# 1. Identity and Profile

| Parameter | Valid value | What it does | Increasing / changing it |
| --- | --- | --- | --- |
| `id` | Unique string | Identifies Redis state, database conversations, API routes, and the persona file. | Do **not** change after real conversations exist. Create a new persona instead. |
| `version` | Integer, normally `1+` | Metadata for future persona migrations. | No current behaviour effect. Increase only after a configuration-shape change. |
| `profile.name` | String | Display name and prompt identity. | Changes the UI name and the name the model uses for itself. |
| `profile.dp` | Served image path | Avatar in the chat UI. | Visual effect only. Example: `/assets/img/munazza.jpeg`. |
| `profile.gender` | String | Enables the current biological-cycle logic only when set to `female`. | Does not itself define personality or reply style. |
| `profile.age_band` | Short string | Background context in the model prompt. | No direct behaviour formula; it influences how the model frames the character. |
| `profile.timezone` | IANA timezone, e.g. `Asia/Karachi` | Selects local hours for presence, busy state, and self-trigger windows. | Moving to another timezone shifts when the persona is likely to be online/busy. Invalid values fall back to UTC. |
| `profile.language_style` | Array of language/style names | Prompt guidance for language and vocabulary. | Add `roman_urdu` for Roman Urdu tendency; add `en` for English tendency. It guides responses naturally rather than forcing every reply into one language. |
| `profile.bio` | String | Stable background and identity context for generation. | More specific, grounded information produces more consistent responses. Avoid temporary moods or scripted messages. |

---

# 2. Personality Traits

All traits are usually `0.0` to `1.0`. They are passed to the LangGraph response prompt as long-term personality context.

| Parameter | Low value tends toward | High value tends toward |
| --- | --- | --- |
| `traits.warmth` | More reserved, emotionally restrained | Kinder, more reassuring, more emotionally open |
| `traits.curiosity` | Fewer questions and less interest in details | More follow-up questions and interest in the other person |
| `traits.assertiveness` | Softer, more hesitant opinions | More direct opinions and clearer boundaries |
| `traits.playfulness` | Serious, literal tone | Teasing, humour, light conversation |
| `traits.romanticism` | Practical or neutral relationship tone | More sentimental tone when the conversation supports it |
| `traits.patience` | Shorter tolerance for repetitive or difficult interactions | More willingness to explain, wait, and continue difficult conversations |
| `traits.social_energy` | Quieter, shorter, lower-engagement replies | More chatty, engaged, socially active replies |
| `traits.privacy` | More willing to volunteer personal information | More guarded and selective about personal details |

> These traits guide model generation. They are not yet individual numeric multipliers in `BehaviorEngine`; availability and mood control reply timing directly.

---

# 3. Mood System

## Mood dimensions

`mood.dimensions` lists the emotional state fields that are stored and decayed over time. Keep `mood.baseline` and `mood.event_weights` aligned with these names.

| Dimension | Higher value changes |
| --- | --- |
| `valence` | More positive overall emotional tone in generated responses |
| `arousal` | More energy; slightly increases online chance and reduces delay tendency |
| `irritability` | More impatience; reduces reply willingness and raises delayed-reply tendency |
| `affection` | More warmth toward the conversation; increases reply willingness |
| `curiosity` | More interest and questions; increases reply willingness |
| `fear` | More caution/guardedness; lowers online chance and reply willingness |

## Baseline mood

`mood.baseline.<dimension>` is normally between `-1.0` and `1.0`.

It is the resting value that mood returns to after an event. For example:

- Raise `baseline.affection` to make the persona generally more receptive.
- Raise `baseline.irritability` to make delayed or declined replies more common.
- Raise `baseline.arousal` to make the persona feel more energetic and somewhat more available.
- Raise `baseline.fear` to make the persona less available and less likely to reply.

## Mood decay

`mood.decay_per_hour` is normally `0.0` to `1.0`.

| Value | Behaviour impact |
| --- | --- |
| Near `0.0` | Emotional events linger for a long time. |
| Around `0.05` to `0.15` | Gradual, human-like settling toward baseline. |
| Near `1.0` | Event effects disappear very quickly. |

## Event weights

`mood.event_weights.<event>.<dimension>` changes mood when the AI classification node identifies an incoming message as an event.

- Positive number: raises that mood dimension.
- Negative number: lowers that mood dimension.
- Suggested range: `-0.25` to `0.25` per event; large values create abrupt personality shifts.

Example: increasing `conflict.irritability` from `0.18` to `0.35` makes a conflict more likely to cause delayed or absent replies afterward.

---

# 4. Availability and Busy Behaviour

## Online probability by hour

`availability.online_probability_by_hour` must contain exactly **24 probabilities**.

This is the base chance that the lifecycle marks the persona online at a given local hour. Mood adjusts it slightly:

- Higher arousal increases it by up to `0.08`.
- Higher fear decreases it by up to `0.12`.

| Value at a given hour | Result |
| --- | --- |
| `0.05` to `0.20` | Persona is rarely online in that hour. Good for sleep hours. |
| `0.35` to `0.60` | Intermittently available. |
| `0.65` to `0.85` | Usually online. |
| `1.0` | Always online at that hour; usually feels artificial. |

## Busy probability by hour

`availability.busy_probability_by_hour` must also contain exactly **24 probabilities**.

This applies only after the persona is online. A busy persona can be online but will not reply immediately; unread messages are scheduled as delayed replies.

| Value at a given hour | Result |
| --- | --- |
| `0.0` to `0.20` | Usually free to engage. |
| `0.30` to `0.60` | Often checking messages but occupied. |
| `0.65` to `0.90` | Frequently busy; delayed replies become common. |

Use high values during work, study, commute, sleep-preparation, or family hours instead of reducing online probability to zero. That creates the more believable state of “online but not available.”

## Reading and replying

| Parameter | Valid range | Lower value | Higher value |
| --- | --- | --- | --- |
| `availability.read_probability_when_online` | `0.0`–`1.0` | More messages remain delivered/unread until another presence cycle. | More messages are noticed and receive read receipts. |
| `availability.reply_probability_when_seen` | `0.0`–`1.0` | More seen messages receive no reply. | More seen messages receive a reply. Mood still modifies this. |

## Reply delay

`availability.delay_seconds` controls delayed responses.

| Parameter | Limit | Behaviour impact |
| --- | --- | --- |
| `min` | Positive seconds | Earliest possible delayed response. Keep above a few seconds. |
| `median` | Between `min` and `max` | Typical delayed-response time. Raise it for a slower texter. |
| `max` | At least `min` | Latest possible delayed response. Raise it for longer, more realistic gaps. |

Message length and low arousal can increase the actual delay before it is clamped to this range.

---

# 5. Persona-Initiated Follow-Ups

`self_trigger` controls messages initiated by the persona after the human has been inactive. It is deliberately range-based rather than a fixed “three days” rule.

## Main controls

| Parameter | Valid value | Lower value | Higher value |
| --- | --- | --- | --- |
| `self_trigger.enabled` | `true` / `false` | `false` disables all autonomous messages. | `true` enables eligible follow-ups. |
| `self_trigger.daily_budget` | Integer `0+` | Fewer autonomous messages per persona-local day. `0` disables them. | Allows more follow-ups across inactive conversations. Values above `2` may feel overly proactive. |
| `self_trigger.idle_minutes_before_follow_up.min` | Minutes, `>= 1` | Earliest an inactive conversation can be considered. | Makes the persona wait longer before initiating. |
| `self_trigger.idle_minutes_before_follow_up.max` | Minutes, `>= min` | Narrows the possible waiting period. | Makes initiation timing more varied and can make the persona more distant. |
| `self_trigger.idle_minutes_before_follow_up.mode` | Between `min` and `max` | Makes shorter idle periods most likely. | Makes longer idle periods most likely. |
| `self_trigger.cooldown_minutes.min|max|mode` | Minutes | Allows another check-in sooner after one has been queued. | Prevents repeated follow-ups to the same inactive conversation for longer. |
| `self_trigger.delay_seconds.min|max|mode` | Seconds | Sends after a shorter natural delay. | Introduces a longer, less perfectly timed delay before the autonomous message. |

### How idle ranges work

The application samples one threshold from a triangular distribution for each conversation after the user's latest message:

- `min` is the earliest possible threshold.
- `max` is the latest possible threshold.
- `mode` is the most likely threshold.

That threshold stays stable until the human sends another message. This avoids re-rolling the decision every 30-second lifecycle tick.

Examples:

| Persona style | `idle_minutes_before_follow_up` |
| --- | --- |
| Proactive friend | `{ "min": 60, "max": 720, "mode": 240 }` |
| Balanced companion | `{ "min": 720, "max": 4320, "mode": 2160 }` |
| Distant / reserved persona | `{ "min": 2880, "max": 10080, "mode": 5760 }` |

## Time windows

`self_trigger.time_windows` is an array of `[start_hour, end_hour]` pairs in local persona time.

```json
"time_windows": [[8, 11], [13, 16], [19, 23]]
```

- An empty array allows initiation at any hour.
- Keep `start_hour <= end_hour`.
- A window does not span midnight. Use two windows for overnight behaviour, for example `[[22, 23], [0, 1]]`.

## Trigger types and weights

Each `self_trigger.triggers` item has a `type` and `weight`.

| Type | Natural intent passed to the response graph |
| --- | --- |
| `morning_greeting` | A brief greeting appropriate for the time of day |
| `idle_follow_up` | A low-pressure check-in after silence |
| `random_thought` | A spontaneous, relevant thought or topic |
| `affection_checkin` | A warmer check-in when the relationship supports it |

Weights are relative; they do not need to total `1.0`.

```json
"triggers": [
  { "type": "idle_follow_up", "weight": 0.55 },
  { "type": "random_thought", "weight": 0.25 },
  { "type": "morning_greeting", "weight": 0.20 }
]
```

Increasing a trigger's weight makes that initiative more likely when a follow-up is selected. A weight of `0` effectively disables it.

---

# 6. Biological Cycle

| Parameter | Effect |
| --- | --- |
| `biological_cycle.enabled` | Enables cycle modifiers only when `profile.gender` is `female`. |
| `biological_cycle.type`, `cycle_days`, `phase_offsets_days` | Metadata at present; phase selection currently follows the calendar day logic in `MoodEngine`. |
| `biological_cycle.modifiers.<phase>.<dimension>` | Adds mood adjustment during a phase when the dimension exists in `mood.dimensions`. |

Use only existing mood dimension names for an active effect. For example, `irritability`, `curiosity`, and `affection` work now. `energy` and `sensitivity` are currently stored as metadata only because they are not current mood dimensions.

---

# 7. Conversation Style and Boundaries

The following fields are preserved in the persona JSON and are useful design metadata, but are **not yet enforced as direct runtime controls**:

- `conversation.max_context_messages` — the graph currently retrieves the latest 15 messages.
- `conversation.silence_after_conflict_minutes`
- `conversation.preferred_greetings`
- `conversation.style.emoji_probability`
- `conversation.style.double_text_probability`
- `conversation.style.short_reply_probability`
- `boundaries.no_claims_of_real_world_presence`
- `boundaries.no_hidden_actions_without_event`
- `boundaries.tools_require_policy_approval`

Changing these fields does not guarantee a current behaviour change. They are the next best candidates for explicit prompt, policy, or behaviour nodes in the LangGraph workflow.

---

# 8. Current Decision Flow

```text
Human message
  -> LangGraph classifies mood event
  -> MoodEngine updates mood state
  -> Lifecycle presence says online/offline + busy/free
  -> BehaviorEngine decides no reply / delayed reply / reply now
  -> If replying: LangGraph retrieves context and memories
  -> LangGraph builds prompt, generates reply, saves memories, validates output
  -> Worker publishes status and message over WebSocket
```

This separation is intentional: personality and mood influence the response, while presence and busy state decide whether a response is plausible at that moment.
