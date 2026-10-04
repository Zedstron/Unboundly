# Persona Simulation Project - Architecture & Agent Reference Manual

This document provides a technical map, architecture, data schemas, and runtime flows for the Persona Simulation engine. It is structured so that any agent or developer (Codex, Claude, GPT, etc.) can skip exploratory phases and understand the system immediately.

---

## 1. System Overview

The **Persona Simulation Engine** simulates human texting behavior for realistic conversational companions. It handles affective mood dynamics, biological cycles, probabilistic online/busy presence, short & long-term memory retrieval, contacts and hidden trust dynamics, as well as multi-transport bridging (WhatsApp, Instagram, and Web UI).

### Core Components
- **Affective State & Biological Cycle**: Mood dynamics across 6 dimensions (`valence`, `arousal`, `irritability`, `affection`, `curiosity`, `fear`) modulated by menstrual phase offsets and hourly decay to baseline.
- **Behavior Engine**: Evaluates circadian availability curves, busyness, and idle time to output one of three decisions: `no_reply`, `reply_scheduled` (late reply), or `reply_now`.
- **Contact & Trust System**: Persistent contact directory in SQLite. Every contact has a hidden `trust` score $\in [-1.0, 1.0]$. Unknown numbers default to $0.0$ trust and can be configured to be ignored via `.env`.
- **LangGraph Agent Workflow**: Orchestrates event classification, memory fetch, system prompt compilation with contact relationship context, and a parallel two-agent fan-out: a message agent (plain-text reply generation with tool-use loop) and a memory agent (structured memory/trust decision), joined by memory/trust persistence.
- **Social Awareness**: Detects pestering contacts from rolling message windows, maintains a durable per-contact annoyance score, and lets the persona confide in a high-trust contact when someone won't stop messaging.
- **Background Worker & Scheduler**: Periodically triggers presence life ticks (`life_tick`), schedules delayed replies via Redis sorted sets, and executes proactive follow-ups and `confide` (venting) tasks.
- **Transport Routing**: Clean decoupling between bridge channels (WhatsApp/Instagram) and Web UI real-time pub/sub.

---

## 2. Directory & Module Map

```
persona/
├── app/
│   ├── main.py                  # FastAPI app entry point & lifespan handler
│   ├── api/
│   │   └── routes.py            # REST API (messages, contacts, state, persona) & WebSocket
│   ├── web/
│   │   └── routes.py            # Web UI template route
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (.env configuration loader)
│   │   ├── lifecycle.py         # AppContext: startup & shutdown for Redis, DB, MCP, bridges
│   │   ├── logger.py            # Central logger
│   │   ├── models.py            # SQLAlchemy models: ConversationMessage, PersonaState, Contact
│   │   ├── prompts.py           # System prompts: persona, event classification, MCP tools
│   │   └── web.py               # Jinja2 template setup
│   ├── domain/
│   │   ├── behavior.py          # BehaviorEngine: online/busy probability & reply decision logic
│   │   ├── mood.py              # MoodEngine: MoodState, decay math, biological cycle modifier
│   │   ├── awareness.py         # Pester detection, contact annoyance, confidant selection
│   │   └── models.py            # Pydantic models: AgentResponse, Memory, ContactInfo, MessageIn
│   ├── infrastructure/
│   │   ├── ai.py                # AIProvider: OpenAI-compatible client, tool execution, embeddings
│   │   ├── memory.py            # ShortTermMemory (Redis), contact activity windows, reply/confide claims, LongTermMemory
│   │   ├── mcp.py               # Model Context Protocol client registry
│   │   ├── persona.py           # PersonaStore: filesystem JSON persona definitions
│   │   └── sqlite.py            # Async SQLite layer: conversations, persona state, contacts/trust/annoyance
│   ├── agents/
│   │   ├── persona_graph.py     # LangGraph StateGraph builder and entry points
│   │   ├── state.py             # PersonaGraphState TypedDict schema
│   │   └── nodes/
│   │       ├── classify.py      # LLM event classification node
│   │       ├── memory_fetch.py  # Context fetch: conversation history, memories, contact info
│   │       ├── preprocessing.py # Assembles system prompt with contact/trust context
│   │       ├── generation.py    # LLM chat completion & tool execution
│   │       ├── memory_update.py # Memory storage & contact trust auto-updater
│   │       └── postprocessing.py# Output response sanitizer
│   └── services/
│       ├── conversation.py      # ConversationService: ingest, awareness/confide, reply generation, life_tick, presence
│       ├── workers.py           # Background async worker loop: scheduled tasks (reply/memory/follow_up/confide) & life ticks
│       └── bridges/             # Social bridge providers (WhatsApp, Instagram)
│           ├── base.py          # SocialBridge interface
│           ├── future.py        # AwaitableFuture implementation
│           ├── models.py        # SocialMessage, Operation enum
│           ├── registry.py      # BridgeRegistry dispatch
│           └── providers/
│               ├── instagram.py # instagrapi client with realtime thread
│               └── whatsapp.py  # WPPConnect client
├── personas/
│   ├── munazza.json             # Persona personality & behavioral definition
│   └── data/
│       └── conversations.db     # SQLite persistence database
├── scripts/
│   └── contacts.py              # Interactive CLI contact & trust management utility
├── tests/                       # Pytest unit & integration test suite
└── ui/                          # Frontend assets (HTML, CSS, JS)
```

---

## 3. Database Schemas (SQLite: `personas/data/conversations.db`)

### `contacts`
| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PK AUTOINCREMENT` | Internal contact identifier |
| `persona_id` | `VARCHAR(50)` | Persona ID (indexed) |
| `contact_id` | `VARCHAR(255)` | External contact ID / phone number / chat ID (indexed) |
| `name` | `VARCHAR(255)` | Contact display name |
| `trust` | `FLOAT` | Trust factor score $\in [-1.0, 1.0]$ (default: `0.0`) |
| `source` | `VARCHAR(30)` | Origin platform (`local`, `whatsapp`, `instagram`) |
| `relationship` | `VARCHAR(30)` | Relationship stage derived from trust, or pinned by admin |
| `is_unknown` | `BOOLEAN` | `True` until the contact is explicitly vetted (default: `True`) |
| `annoyance` | `FLOAT` | Durable pester score $\in [0.0, 1.0]$, decays over time (default: `0.0`) |
| `created_at` | `TIMESTAMP` | Record creation timestamp (UTC) |
| `updated_at` | `TIMESTAMP` | Record update timestamp (UTC) |
*Unique constraint / index*: `(persona_id, contact_id)`

### `conversation_messages`
| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PK AUTOINCREMENT` | Primary key |
| `persona_id` | `VARCHAR(50)` | Persona ID |
| `conversation_id` | `VARCHAR(100)` | Conversation identifier |
| `direction` | `VARCHAR(20)` | `user` or `bot` |
| `status` | `VARCHAR(50)` | `delivered` or `seen` |
| `content` | `TEXT` | Message text content |
| `source` | `VARCHAR(30)` | Message source (`local`, `whatsapp`, `instagram`) |
| `external_id` | `VARCHAR(255)` | Message ID from external provider |
| `sender_id` | `VARCHAR(255)` | Sender identifier |
| `sender_name` | `VARCHAR(255)` | Sender display name |
| `created_at` | `TIMESTAMP` | Message timestamp (UTC) |

### `persona_states`
| Column | Type | Description |
|---|---|---|
| `persona_id` | `VARCHAR(50) PK` | Primary key |
| `mood` | `JSON` | Dict of current mood dimension values |
| `updated_at` | `TIMESTAMP` | Last updated timestamp (UTC) |

---

## 4. Key Runtime Workflows

### Ingestion Flow (`ConversationService.ingest`):
1. **Idempotency Guard**: For bridge messages, drops a message whose `(persona_id, source, external_id)` is already stored. A re-emitted/replayed provider event cannot create a duplicate conversation row or a second reply.
2. **State Load**: Loads the current mood state from SQLite (falling back to Redis), then persists any refresh back to both stores.
3. **Save Message**: Writes the user message to the `conversation_messages` table.
4. **Social Awareness** (only when `social_awareness.enabled`): records the message in a rolling Redis activity window, derives the pester level, applies the `pestering`/`boundary_push` mood event, and decays+raises the contact's `annoyance` score.
5. **Confide Check**: At `annoyed` level or above, may queue one rate-limited `confide` task directed at a high-trust confidant (see Social Awareness section).
6. **Unknown Contact Check**: Checks if the sender is a saved contact. If unknown and `REPLY_UNKNOWN_CONTACTS=0`, ingestion stops and no reply is scheduled (memory-only task still queued).
7. **Behavior Decision**: Computes `Decision` based on presence, busyness, and mood:
   - `NO_REPLY`: No action (memory-only task).
   - `LATE_REPLY`: Calculates delay in seconds and queues `reply_pending` task in Redis sorted set (`persona:schedule`).
   - `REPLY_NOW`: Queues `reply_pending` task in Redis for immediate execution.
   - Every reply task acquires a per-message Redis claim (`persona:reply:queued:{persona}:{message_id}`), so the live path and the unread sweep can never queue two replies for one message.

### Worker & Reply Generation (`worker_task`):
1. **Life Tick (every 30s)**: Evaluates hourly online/busy probability curve for each persona. If presence transitions to online, processes unread messages.
2. **Due Tasks (every 1s)**: Reads and claims ready tasks from `persona:schedule` (zrem per item, so concurrent workers cannot double-process).
3. **Task Types**: `memory_pending` (memory only, no reply), `reply_pending` (marks the inbound message seen, then generates), `follow_up` (proactive persona initiation), and `confide` (persona vents to a confidant about a pestering contact; generates with `initiative="venting"` and a sanitized confide context).
4. **Reply Execution via LangGraph**:
   - `retrieve_context_node`: Fetches 15 recent messages, short-term memories, semantic long-term memories, and contact/trust information.
   - `build_prompt_node`: Compiles persona traits, mood state, contact status (unknown vs known and current trust), and memory context into system prompt.
   - `generate_response_node`: Message agent — generates only the reply text via OpenAI-compatible endpoint with tool-use loop support.
   - `decide_memories_node`: Memory agent — runs in parallel with the message agent over the latest exchange and recalled memories; decides which facts to store and the `trust_factor` delta via a structured JSON schema call (temperature 0). Degrades to a no-op on LLM failure.
   - `save_memories_node`: Stores the memory agent's extracted facts and applies its `trust_factor` delta to update the contact's trust score in SQLite.
   - `postprocess_response_node`: Strips and validates reply text.
5. **Outbound Routing**:
   - If message source is `whatsapp` or `instagram`, reply and seen events are sent strictly to the bridge interface.
   - If message source is `local` (or unset), reply and seen events are published strictly to Redis Web UI pub/sub channels (`persona:out:{persona_id}:{conversation_id}`).

### Social Awareness & Confide (`app/domain/awareness.py`):

When a persona enables `social_awareness`, it models the real-life habit of growing tired of a pest and confiding in someone trusted.

1. **Activity windows** — `ShortTermMemory.record_contact_message` / `get_contact_activity` keep a 24h Redis sorted set per `(persona, contact)` and return `burst_2m`, `sustained_10m`, `hourly`, and `daily` counts.
2. **Pester level** — `evaluate_pestering(activity, unanswered, is_unknown, thresholds)` also folds in `count_unanswered_user_messages(persona, conversation)` and returns `neutral | notice | annoyed | harassed`. Unknown contacts use strict thresholds; known contacts are scaled 2x more leniently.
3. **Mood + annoyance** — `mood_event(level)` maps `notice`/`annoyed` to `pestering` and `harassed` to `boundary_push`; `_bump_annoyance` decays then raises `contacts.annoyance`.
4. **Confidant selection** — `select_confidant(contacts, min_trust, exclude_id)` returns the highest-trust contact that is `is_unknown=False` and `trust >= social_awareness.min_confidant_trust`, always excluding the offender.
5. **Confide task** — `_maybe_schedule_confide` requires level >= `annoyed`, the persona online and not busy, an active `time_windows` hour, and passes three limits: a per `(offender, confidant)` cooldown (`claim_confide`), a persona-wide cooldown (`claim_confide_global`), and a daily budget. It schedules a `confide` task with `trigger="venting"` and a redacted `confide_context`.

Prompt wiring: the worker passes `confide_context` through `ConversationService.generate_reply` -> `PersonaAgentGraph.generate_reply` -> `PersonaGraphState.confide_context` -> `build_prompt_node`, which appends a `CONFIDING IN THIS PERSON` block. `share_contact_identity` defaults to `false`, so the sender is never named and phone numbers/usernames are never exposed.

---

## 5. Environment Configuration (`.env`)

```ini
APP_ENV=dev
IDENTITY_MODE=both              # Options: local, bridge, both
REPLY_UNKNOWN_CONTACTS=0       # 0 = Ignore unknown numbers, 1 = Allow replying to unknown numbers

REDIS_URL=redis://localhost:6379/0

LLM_BASE_URL=https://api.x.ai/v1
LLM_API_KEY=your_llm_key
LLM_MODEL=grok-4.3

LOCAL_EMBEDDING_GENERATOR=1
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

WPPBRIDGE_ENABLED=0
INSTABRIDGE_ENABLED=0
```

> **Social awareness is persona-level, not env-level.** Configure it inside the persona JSON under `social_awareness` (see `personas/munazza.json`), including `enabled`, `min_confidant_trust`, `share_contact_identity`, cooldowns, `daily_confide_budget`, `time_windows`, and per-window `thresholds`. It is inert unless `enabled: true`.

---

## 6. Developer & Testing Commands

- **Run Test Suite**:
  ```powershell
  .venv\Scripts\python -m pytest tests/test_contacts_and_trust.py tests/test_behavior.py tests/test_persona_graph.py tests/test_persona_state_persistence.py tests/test_prompts.py
  ```
- **Manage Contacts CLI**:
  ```powershell
  .venv\Scripts\python scripts/contacts.py
  ```
- **Start Web Application**:
  ```powershell
  .venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
  ```
