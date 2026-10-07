<div align="center">

<img src="https://img.shields.io/badge/Introducing-Unboundly-6C5CE7?style=for-the-badge&logo=robotframework&logoColor=white" alt="Unboundly Banner" />

# 🎭 Unboundly

### *No script. No leash. Just presence.*

**A mood-driven conversational engine for building emotionally realistic AI personas — with authentic moods, evolving personalities, long-term memory, trust, and human-like behavioral patterns.**

<p>
  <img src="https://img.shields.io/badge/status-under%20development-orange?style=flat-square" alt="status" />
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square&logo=python&logoColor=white" alt="python version" />
  <img src="https://img.shields.io/badge/FastAPI-async-009688?style=flat-square&logo=fastapi&logoColor=white" alt="fastapi" />
  <img src="https://img.shields.io/badge/LangGraph-agent%20workflow-1C3C3C?style=flat-square" alt="langgraph" />
  <img src="https://img.shields.io/badge/Redis-memory%20%26%20queue-DC382D?style=flat-square&logo=redis&logoColor=white" alt="redis" />
  <img src="https://img.shields.io/badge/version-0.1.0--prerelease-informational?style=flat-square" alt="version" />
  <img src="https://img.shields.io/badge/license-GPL--3.0-blue?style=flat-square" alt="license: GPL-3.0" />
</p>

[Overview](#-introducing-unboundly) • [Features](#-why-unboundly) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Architecture](#-architecture-overview) • [Persona Config](#-persona-configuration) • [Roadmap](#-roadmap--known-limitations) • [Contributing](#-contributing)

</div>

> **⚠️ Status: Under Active Development**
> This project is **not stable** and is evolving fast. APIs, schemas, and architecture are subject to change without notice — pin your dependencies accordingly.

---

## 🌟 Introducing Unboundly

Meet **Unboundly** — a mood-driven conversational AI framework for simulating realistic, emotionally-aware virtual personas — not scripted bots. Built with **Python**, **FastAPI**, **LangGraph**, and **Redis**, it powers characters that behave like actual people: they get busy, they get moody, they take time to reply, they remember you, they build (or lose) trust, and they sometimes reach out first.

Unlike typical LLM wrappers that generate an instant, uniform reply to every message, Unboundly layers a **behavioral simulation engine** on top of your favorite language model (local via **LM Studio** or hosted via any **OpenAI-compatible API**) to decide *if*, *when*, and *how* a persona responds.

The name says it all: personas here aren't bound to instant replies, aren't bound to a single scripted response, aren't bound by rigid rules. They're **unbound** — free to have a bad day, a curious streak, or a moment of silence, just like a real person would.

### 🧠 The Core Idea: Mood-Driven Behavior

A persona's engagement is shaped by:

- 💓 **Current emotional state** — valence, arousal, irritability, affection, curiosity, fear
- 🕐 **Time of day & availability** — personas aren't online 24/7
- 💬 **Conversation history & events** — every interaction leaves a trace
- 🧠 **Short & long-term memory** — recent context plus semantically recalled facts
- 🤝 **Contact trust & relationship stage** — the same sentence means something different to a stranger and to a partner
- 🎨 **Personality traits** — warmth, assertiveness, playfulness, and more
- 🔔 **Self-triggered follow-ups** — personas can initiate messages, not just react

The result: virtual characters with a genuine sense of presence, pacing, and personality — ideal for narrative AI, companion apps, simulation research, and next-gen conversational UX.

---

## ✨ Why Unboundly?

| | |
|---|---|
| 🎭 **Authentic Personas** | Rich JSON-defined characters with configurable traits and biography |
| 🕵️ **Social Awareness** | Detects pestering contacts, tracks annoyance, and confides in a trusted confidant — even about strangers it never replies to |
| 🌊 **Dynamic Mood Engine** | Multi-dimensional emotional states that evolve, decay, and react to events |
| ⏳ **Realistic Pacing** | Probabilistic online/busy/reply behavior instead of instant robotic replies |
| 🤝 **Trust & Relationships** | Hidden per-contact trust score that maps to named relationship stages |
| 🧠 **Two-Tier Memory** | Redis short-term context + semantic long-term vector recall |
| 🕸️ **LangGraph Agent** | Classify → retrieve → prompt → generate, with a parallel memory agent |
| 💬 **Message Types** | Persona answers as JSON: plain text, a quoted reply, or an emoji reaction |
| 🔌 **Multi-Transport Bridges** | Attach real WhatsApp and Instagram identities, or run purely local |
| 🛠️ **MCP Tool Use** | Optional Model Context Protocol tools available to the persona |
| ⚡ **Modern Stack** | FastAPI + LangGraph + Redis + SQLite for speed, memory, and persistence |
| 🧩 **Extensible Architecture** | Clean domain-driven layers make it easy to customize behavior logic |

---

## 🛠️ Requirements

| Requirement | Details |
|---|---|
| **Python** | 3.12 or higher |
| **Docker Desktop** | Required on Windows for Redis |
| **AI Provider** | [LM Studio](https://lmstudio.ai/) (recommended, local) *or* any OpenAI-compatible API |

---

## 📦 Installation

### 1. Install Python 3.12+
Download from [python.org](https://www.python.org/downloads/) and make sure it's added to your `PATH`.

### 2. Install Docker Desktop
Get it from [Docker's official site](https://www.docker.com/products/docker-desktop) — required for running Redis on Windows.

### 3. Install Project Dependencies

```bash
pip install -e ".[dev]"
```

This pulls in everything defined in `pyproject.toml`, including:

- ⚡ **FastAPI** + **Uvicorn** — web framework and ASGI server
- 🕸️ **LangGraph** — agent workflow orchestration
- 🧵 **Redis / redisvl** — memory, scheduling, pub/sub
- ✅ **Pydantic** — data validation
- 🤖 **OpenAI client** — LM Studio / OpenAI-compatible inference
- 🌉 **WPP_Whatsapp** + **instagrapi** — optional WhatsApp / Instagram bridges
- 🧰 **MCP** — optional Model Context Protocol tool support

### 4. Configure Your Environment

Copy the template and edit it:

```bash
cp .env.example .env
```

Minimal local setup (LM Studio on port `1234`):

```env
IDENTITY_MODE=local
REDIS_URL=redis://localhost:6379/0

LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=not-needed
LLM_MODEL=your-model-name

EMBEDDING_BASE_URL=http://localhost:1234/v1
EMBEDDING_API_KEY=not-needed
EMBEDDING_MODEL=your-embedding-model
```

For a fully offline embedding generator instead of a hosted endpoint:

```env
LOCAL_EMBEDDING_GENERATOR=1
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

---

## 🚀 Quick Start

### 1. Spin up Redis via Docker

```bash
docker-compose up -d
```

Starts **Redis Stack** on port `6379` (and RedisInsight on `8001`) — required for memory, scheduling, and pub/sub.

### 2. Launch the Server

```bash
uvicorn app.main:app --reload
```

| Endpoint | URL |
|---|---|
| 🌐 Web Interface | `http://localhost:8000/` |
| 📖 API Docs (Swagger) | `http://localhost:8000/docs` |

### 3. Start LM Studio (if using a local model)

1. Open **LM Studio**
2. Load a compatible chat model (and an embedding model if you use hosted embeddings)
3. Start the local inference server on `http://localhost:1234`

You're live! 🎉

---

## 🏗️ Architecture Overview

Unboundly follows a clean, layered, domain-driven design, with a **LangGraph** agent workflow at its core.

```
Inbound Message (local UI · WhatsApp · Instagram)
    │
    ▼
ConversationService.ingest
    ├── Idempotency guard (drop re-delivered provider messages)
    ├── Load persona + current mood state
    ├── Persist message (SQLite) + tag replies
    ├── Social awareness → pester level · annoyance · mood event
    ├── Confidant check → queue rate-limited `confide` task
    │      (runs BEFORE the unknown-contact gate — a stranger can trigger a
    │       vent to a loved one; see Social Awareness & Pestering below)
    ├── Unknown-contact gate (REPLY_UNKNOWN_CONTACTS)
    └── Behavior Engine decision
          ├── NO_REPLY       → queue memory-only task
          ├── LATE_REPLY     → queue reply task with a delay
          └── REPLY_NOW      → queue reply task immediately
    │
    ▼
Background Worker (Redis sorted-set scheduler + 30s life ticks)
    ├── reply_pending · memory_pending · follow_up
    └── confide (persona vents about a pestering contact)
    │
    ▼
LangGraph reply workflow
    ├── retrieve_context   (history · short memory · long memory · contact/trust)
    ├── build_prompt       (persona · mood · relationship · reply/confide context · memories)
    ├── generate_response  (message agent — JSON: text | reply | reaction)
    └── fan-out ─┬─ decide_memories  (memory agent — facts + trust delta)
                 └─ postprocess_response
    │
    ▼
Outbound routing
    ├── Bridge (WhatsApp/Instagram) → send text / quoted reply / reaction
    └── Local UI → Redis pub/sub (WebSocket)
    │
    ▼
Persist → SQLite (history) · Redis (mood + memory + presence)
```

### 📁 Layer Breakdown

<details>
<summary><strong>🧱 <code>app/core/</code> — Foundation & Configuration</strong></summary>

- `config.py` — Application settings & environment variables (Pydantic Settings)
- `logger.py` — Structured, colorized logging
- `models.py` — SQLAlchemy ORM models (`ConversationMessage`, `PersonaState`, `Contact`)
- `prompts.py` — System prompts: persona, memory decision, event classification, MCP tools
- `lifecycle.py` — `AppContext`: startup/shutdown for Redis, DB, MCP, and bridges
- `web.py` — Jinja2 template setup

</details>

<details>
<summary><strong>🧠 <code>app/domain/</code> — Business Logic Engine</strong></summary>

- `behavior.py` — **Behavior Engine**: decides if/when a persona responds, from mood, availability, and context
- `mood.py` — **Mood State Management**: valence, arousal, irritability, affection, curiosity, fear, with time-based decay and biological-cycle modifiers
- `awareness.py` — **Social Awareness**: classifies pestering contacts (neutral → notice → annoyed → harassed), maps them to mood events, and selects a trusted confidant
- `relationship.py` — **Relationship Stages**: maps the hidden trust score to named stages (stranger → engaged → family / blocked) and renders prompt guidance
- `models.py` — Core domain models (`Decision`, `AgentResponse`, `Memory`, `MemoryDecision`, `MessageIn`, `ContactInfo`)

</details>

<details>
<summary><strong>🔌 <code>app/infrastructure/</code> — External System Integration</strong></summary>

- `ai.py` — AI provider (OpenAI-compatible): chat, tool-use loop, structured memory decisions, embeddings
- `memory.py` — **ShortTermMemory** (Redis list) plus rolling contact-activity windows, reply/confide claims, and **LongTermMemory** (Redis vector store with cosine search)
- `sqlite.py` — Async SQLite persistence (conversations, persona state, contacts/trust/annoyance)
- `persona.py` — `PersonaStore`: filesystem JSON persona definitions
- `mcp.py` — Model Context Protocol client registry for optional tool use

</details>

<details>
<summary><strong>🕸️ <code>app/agents/</code> — LangGraph Workflow</strong></summary>

- `persona_graph.py` — Builds the `StateGraph` and exposes `classify_event`, `generate_reply`, `remember_message`
- `state.py` — `PersonaGraphState` schema
- `nodes/` — `classify`, `memory_fetch`, `preprocessing`, `generation`, `memory_update`, `postprocessing`, `remember`

</details>

<details>
<summary><strong>🧭 <code>app/services/</code> — High-Level Orchestration</strong></summary>

- `conversation.py` — Orchestrates ingestion, mood/behavior, reply generation, follow-ups, presence
- `workers.py` — Background worker: life ticks, scheduled tasks, outbound routing (text/reply/reaction)
- `bridges/` — Transport providers with a common `SocialBridge` interface
  - `base.py`, `models.py` (`SocialMessage`, `Operation`), `registry.py`, `future.py`
  - `providers/whatsapp.py` — WPPConnect client (text, reply, reaction, seen, attachments)
  - `providers/instagram.py` — instagrapi client with realtime MQTT thread

</details>

<details>
<summary><strong>🌐 <code>app/api/</code> & <code>app/web/</code> — User Interfaces</strong></summary>

- `api/routes.py` — REST endpoints (messages, conversation, contacts, state, persona) + WebSocket pub/sub
- `web/routes.py` — Serves the chat UI template

</details>

### 🔑 Key Components

1. **Behavior Engine** (`app/domain/behavior.py`) — Probabilistic decision-making: ignore, read, reply later, or reply now.
2. **Mood System** (`app/domain/mood.py`) — Multi-dimensional emotional state that decays over time and shifts with events.
3. **Contact & Trust** (`app/domain/relationship.py`, `app/infrastructure/sqlite.py`) — Persistent contact directory with a hidden `trust ∈ [-1.0, 1.0]`, a derived relationship stage, and a durable `annoyance ∈ [0, 1]` pester score. Unknown numbers default to `0.0` (stranger, `is_unknown=true`) and can be ignored via `.env`.
4. **LangGraph Agent** (`app/agents/persona_graph.py`) — Two-agent fan-out: a message agent (JSON text/reply/reaction) and a memory agent (facts + trust delta), joined by a persistence node.
5. **Memory** (`app/infrastructure/memory.py`) — Short-term Redis context plus semantic long-term vector recall.
6. **Worker & Scheduler** (`app/services/workers.py`) — Life ticks, delayed replies (Redis sorted set), proactive follow-ups, and `confide` (venting) tasks.
7. **Transport Bridges** (`app/services/bridges/`) — WhatsApp and Instagram providers behind one interface, plus local UI pub/sub.
8. **Social Awareness** (`app/domain/awareness.py`) — Pester detection from rolling activity windows, a durable per-contact annoyance score, and confidant selection for the `confide` initiative.

---

## 🎨 Persona Configuration

Personas are defined declaratively in JSON (e.g., `personas/munazza.json`), making it easy to design new characters without touching code.

```json
{
  "id": "persona_id",
  "version": 1,
  "profile": {
    "name": "Display Name",
    "dp": "/assets/img/avatar.jpeg",
    "gender": "gender",
    "age_band": "adult|teen|child",
    "timezone": "Asia/Karachi",
    "language_style": ["en", "roman_urdu"],
    "bio": "Character description"
  },
  "traits": {
    "warmth": 0.78,
    "curiosity": 0.72,
    "assertiveness": 0.48,
    "playfulness": 0.69,
    "romanticism": 0.42,
    "patience": 0.57,
    "social_energy": 0.63,
    "privacy": 0.68
  },
  "mood": {
    "dimensions": ["valence", "arousal", "irritability", "affection", "curiosity", "fear"],
    "baseline": { "...": "baseline mood values 0.0–1.0" },
    "decay_per_hour": 0.08,
    "event_weights": { "...": "mood impacts from specific events" }
  },
  "availability": {
    "online_probability_by_hour": ["24 values, 0.0–1.0"],
    "busy_probability_by_hour": ["24 values, 0.0–1.0"],
    "read_probability_when_online": 0.86,
    "reply_probability_when_seen": 0.78,
    "delay_seconds": { "min": 4, "max": 900, "median": 45 }
  },
  "self_trigger": {
    "enabled": true,
    "daily_budget": 2,
    "idle_minutes_before_follow_up": { "min": 720, "max": 4320, "mode": 2160 },
    "cooldown_minutes": { "min": 1440, "max": 4320, "mode": 2880 },
    "delay_seconds": { "min": 30, "max": 600, "mode": 120 },
    "time_windows": [[8, 11], [13, 16], [19, 23]],
    "triggers": [{ "type": "check_in", "weight": 1 }]
  },
  "social_awareness": {
    "enabled": true,
    "min_confidant_trust": 0.72,
    "share_contact_identity": false,
    "confide_cooldown_minutes": 720,
    "confide_global_cooldown_minutes": 240,
    "daily_confide_budget": 2,
    "confide_delay_seconds": 90,
    "annoyance_decay_per_hour": 0.08,
    "time_windows": [[8, 23]],
    "thresholds": {
      "notice_burst_2m": 3,
      "notice_sustained_10m": 5,
      "notice_unanswered": 4,
      "annoyed_sustained_10m": 8,
      "annoyed_hourly": 12,
      "annoyed_unanswered": 8,
      "harassed_hourly": 15,
      "harassed_daily": 30,
      "harassed_unanswered": 15
    }
  }
}
```

### 🔍 Key Properties

| Field | Description |
|---|---|
| **`traits`** | Core personality dials, each scored `0.0`–`1.0` |
| **`mood.dimensions`** | Emotional axes that respond to events and decay over time |
| **`availability`** | Hour-by-hour online/busy probability and response timing patterns |
| **`event_weights`** | How specific conversation events shift the persona's mood |
| **`self_trigger`** | Rules for persona-initiated follow-ups (windows, idle time, daily budget) |
| **`social_awareness`** | Pester thresholds, confidant trust floor, and confide rate limits (see below) |

---

## 💬 Message Types & Replies

The persona's message agent answers with a small JSON envelope so it can decide, per turn, *how* to respond:

```json
{ "type": "text",     "text": "haha yeah", "reaction": null }
{ "type": "reply",    "text": "because you said tomorrow", "reaction": null }
{ "type": "reaction", "text": null, "reaction": "😂" }
```

| Type | Behaviour |
|---|---|
| **`text`** | A normal message (the default). |
| **`reply`** | A **quoted reply** anchored to a specific earlier message. The agent chooses this when a quote removes ambiguity. |
| **`reaction`** | A single **emoji reaction** on the inbound message instead of a written reply. |
| **`voice` / `image`** | 🔒 **Reserved for future implementation** — accepted by the schema but rejected by the pipeline today. |

**Reply awareness works in both directions:**

- **Inbound** — WhatsApp (`quotedMsgId` / `quotedMsg`) and Instagram (`replied_to_message` / `reply`) quotes are detected and tagged on the message, then surfaced to the agent so it knows a message is a reply and what was quoted.
- **Outbound** — the worker resolves the provider message id and sends a native quoted reply (WhatsApp `reply`, Instagram `direct_send(reply_to_message=…)`) or a native reaction.

---

## 🕵️ Social Awareness & Pestering

Real people push back when someone messages them non-stop — and they tell someone they trust about it. Unboundly models that with a deterministic awareness layer that runs during `ingest`, before the behavior decision:

1. **Detect** — every inbound message is recorded in a rolling Redis window. `evaluate_pestering` combines the counts (`burst_2m`, `sustained_10m`, `hourly`, `daily`) with the unanswered streak to classify the contact as `neutral → notice → annoyed → harassed`. Unknown contacts are held to strict thresholds; known contacts are treated twice as leniently.
2. **Feel** — crossing a level applies the `pestering` (or `boundary_push`) mood event, so irritability/valence shift naturally and the persona's tone changes on its own. A durable `annoyance ∈ [0, 1]` score is also decayed and raised on the contact row.
3. **Confide** — at `annoyed` or above, the persona may queue a `confide` task: a one-off, self-initiated message to its **highest-trust vetted contact** (trust ≥ `min_confidant_trust`, never the offender). The vent is rate-limited per offender+confidant pair, persona-wide, and per day, and is generated by the normal reply pipeline with a sanitized `CONFIDING IN THIS PERSON` prompt block. This check runs **before** the unknown-contact gate — see below for how a stranger triggers it.

Privacy defaults to guarded: `share_contact_identity: false` means the persona talks about "an unsaved contact" rather than naming the sender, and phone numbers/usernames are never exposed.

### ⚠️ Strangers can reach your loved ones: how the confide path works

The confidant check runs **before** the `REPLY_UNKNOWN_CONTACTS` gate, so a contact the persona never replies to can still cause it to proactively message someone close. This is deliberate — real people vent about a stranger who won't stop messaging them, even while refusing to engage that stranger — but the mechanics matter for anyone deploying a persona on a public channel:

- **The escalation is deterministic.** With replies to unknown contacts disabled, no bot message ever lands in the stranger's thread, so the unanswered streak only grows. Against the default thresholds that means **4** unanswered messages → `notice`, **8** → `annoyed` (confide territory), **15** → `harassed`.
- **Who gets told.** `select_confidant` picks the single highest-trust contact that is vetted (`is_unknown=false`) and meets the floor `min_confidant_trust` (default `0.72` — typically a partner/boyfriend-tier contact), excluding the offender. An unknown sender can never be *chosen* as a confidant; it can only be talked *about*.
- **What bounds it.** The persona must be online, not busy, and inside `time_windows`; then three limits apply: the offender+confidant pair cooldown (`confide_cooldown_minutes`, default 720), a persona-wide cooldown (`confide_global_cooldown_minutes`, default 240), and `daily_confide_budget` (default 2). A spammer can therefore force **at most 2 vent messages per day**, and never to the same offender+confidant pair more often than the pair cooldown.
- **What is said.** The vent is generated from a redacted context: by default (`share_contact_identity: false`) the persona describes "an unsaved contact" — the name, phone number, and username never enter the prompt, so they cannot leak. Enabling `share_contact_identity: true` passes the sender's **platform display name** into the prompt; for an unknown contact that is a self-reported string from the sender, so enabling it is a decision to let the persona relay that name to its loved ones.
- **Mood is not budgeted.** Unlike confiding, the `pestering` / `boundary_push` mood events apply on every qualifying inbound message with no cooldown, so a flood from a stranger can hold the persona in a bad mood across *all* conversations until the mood decays.

The only switch that fully prevents stranger-triggered confides today is `enabled: false` (which disables the whole awareness layer for known contacts too). Otherwise, raise `annoyed_unanswered` / `harassed_unanswered` in `social_awareness.thresholds` or accept the daily-budget ceiling above.

> **Note:** configuring `social_awareness` is optional. Leave it absent or `enabled: false` and ingestion behaves exactly as before.

---

## 🌉 Transport Bridges

Unboundly can run **purely local**, **purely bridged**, or **both**, controlled by `IDENTITY_MODE` (`local` | `bridge` | `both`).

### WhatsApp (WPPConnect)

```env
WPPBRIDGE_ENABLED=1
WPPBRIDGE_SESSION=persona_id
WPPBRIDGE_TOKEN_DIR=
WPPBRIDGE_QUEUE_SIZE=50
WPPBRIDGE_HEADLESS=1
WPPBRIDGE_NUMBER=persona_account_number
```

On first run a QR code is printed to the terminal — scan it with WhatsApp. Session tokens are cached in `WPPBRIDGE_TOKEN_DIR` (defaults to `tokens/`).

### Instagram (instagrapi + realtime MQTT)

```env
INSTABRIDGE_ENABLED=1
INSTABRIDGE_USERNAME=
INSTABRIDGE_PASSWORD=
INSTABRIDGE_SESSION_FILE=
INSTABRIDGE_SESSION=persona_id
INSTABRIDGE_AUTH_CODE=000000
```

The Instagram bridge uses a realtime MQTT connection with exponential-backoff reconnection (`INSTABRIDGE_REALTIME_RECONNECT_BASE/MAX`) and a command timeout (`INSTABRIDGE_REALTIME_COMMAND_TIMEOUT`).

---

## 🛠️ Model Context Protocol (MCP)

Give personas real tools by pointing them at remote MCP servers:

```env
MCP_SERVER_URLS=https://tools.example.com/sse,https://search.example.com/sse
```

When tools are available, the message agent runs a tool-use loop and the prompt includes an `[AVAILABLE TOOLS]` block. Leave empty to disable tool use.

---

## 🌐 API Reference

All REST endpoints are served under `/api`, with a live WebSocket at `/api/ws`.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/{pid}/persona` | Fetch a persona definition |
| `PUT` | `/{pid}/persona` | Update a persona definition |
| `GET` | `/{pid}/state` | Current mood state |
| `PUT` | `/{pid}/state` | Set mood values |
| `POST` | `/{pid}/state/reset` · `DELETE /{pid}/state` | Reset mood to baseline |
| `POST` | `/{pid}/messages` | Send a local message (accepts optional reply metadata) |
| `GET` | `/{pid}/conversation/{conversation_id}` | Conversation history (includes reply metadata) |
| `DELETE` | `/{pid}/conversation/{conversation_id}` | Clear a conversation |
| `DELETE` | `/{pid}/conversation/{conversation_id}/messages/{message_id}` | Delete one message |
| `GET` | `/{conversation_id}/personas` | List personas with presence, mood, and last message |
| `GET` | `/{pid}/contacts` · `GET /{pid}/contacts/{contact_id}` | List / fetch contacts |
| `POST` | `/{pid}/contacts` · `PUT /{pid}/contacts/{contact_id}` | Create / update a contact |
| `PATCH` | `/{pid}/contacts/{contact_id}/trust` | Adjust a contact's trust delta |
| `DELETE` | `/{pid}/contacts/{contact_id}` | Delete a contact |
| `WS` | `/api/ws` | Real-time pub/sub (messages, status, typing, presence, reactions) |

---

## 📂 Project Structure

```
persona/
├── app/
│   ├── core/               # Config, logging, ORM models, prompts, lifecycle
│   ├── domain/             # Behavior, mood, awareness, relationship stages, models
│   ├── infrastructure/     # AI, memory, SQLite, persona store, MCP
│   ├── agents/             # LangGraph workflow and nodes
│   ├── services/           # Conversation orchestration, worker, bridges
│   │   └── bridges/
│   │       └── providers/  # WhatsApp, Instagram
│   ├── api/                # REST API + WebSocket routes
│   ├── web/                # Web interface routes
│   └── main.py             # FastAPI app entry point
├── personas/
│   ├── munazza.json        # Persona definition
│   └── data/               # SQLite database (conversations.db)
├── scripts/
│   └── contacts.py         # Interactive contact & trust CLI
├── tests/                  # Pytest suite
├── ui/
│   ├── templates/          # HTML templates
│   └── assets/             # CSS, JS, images
├── docker-compose.yml      # Redis Stack + persona engine
├── pyproject.toml          # Dependencies and project metadata
└── README.md               # This file
```

---

## 🧪 Development

### Running Tests

```bash
pytest
```

### Linting

```bash
ruff check .
```

### Managing Contacts & Trust

An interactive CLI for creating contacts, adjusting trust, pinning relationship stages, and vetting unknown contacts:

```bash
python scripts/contacts.py
```

### Local `.env` Reference

```env
APP_ENV=dev
IDENTITY_MODE=both              # local | bridge | both
REPLY_UNKNOWN_CONTACTS=0        # 0 = ignore unknown numbers, 1 = allow replying

REDIS_URL=redis://localhost:6379/0

LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=
LLM_MODEL=

EMBEDDING_BASE_URL=http://localhost:1234/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=

LOCAL_EMBEDDING_GENERATOR=0
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

LOGGING_ENABLED=true
LOG_LEVEL=INFO
LOG_FILE_PATH=

WPPBRIDGE_ENABLED=0
INSTABRIDGE_ENABLED=0
MCP_SERVER_URLS=
```

---

## 🗺️ Roadmap & Known Limitations

- [x] Mood persistence and historical tracking
- [x] Contact directory with hidden trust and relationship stages
- [x] Long-term semantic memory (Redis vector store)
- [x] WhatsApp & Instagram bridges with realtime ingestion
- [x] Structured message types: text, quoted reply, and reaction
- [x] Social awareness: pestering detection, contact annoyance, and the confide-in-a-confidant initiative
- [ ] **Voice and image message types** (schema reserved; not yet implemented)
- [ ] Multi-persona conversation dynamics
- [ ] More sophisticated context windowing for longer conversations
- [ ] Comprehensive test coverage across all bridges and workers

---

## 🤝 Contributing

This is a research / experimental project, and contributions are very welcome! Please make sure any pull request:

- ✅ Follows the **Ruff** linting standards
- ✅ Includes relevant tests
- ✅ Comes with a clear description of the change

Found a bug or have an idea? [Open an issue](#) — we'd love to hear from you.

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See the [LICENSE](LICENSE) file for the full license text.

## 💬 Contact & Support

For questions, feedback, or issues, please open an issue in the project repository.

---

<div align="center">

**Unboundly** — *No script. No leash. Just presence.*

*Last Updated: October 2026 · Version 0.1.0 (Pre-release)*

</div>
