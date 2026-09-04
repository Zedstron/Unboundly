<div align="center">

<img src="https://img.shields.io/badge/Introducing-Unboundly-6C5CE7?style=for-the-badge&logo=robotframework&logoColor=white" alt="Unboundly Banner" />

# 🎭 Unboundly

### *No script. No leash. Just presence.*

**A mood-driven conversational engine for building emotionally realistic AI personas — with authentic moods, evolving personalities, and human-like behavioral patterns.**

<p>
  <img src="https://img.shields.io/badge/status-under%20development-orange?style=flat-square" alt="status" />
  <img src="https://img.shields.io/badge/python-3.13%2B-blue?style=flat-square&logo=python&logoColor=white" alt="python version" />
  <img src="https://img.shields.io/badge/FastAPI-async-009688?style=flat-square&logo=fastapi&logoColor=white" alt="fastapi" />
  <img src="https://img.shields.io/badge/Redis-cache%20%26%20memory-DC382D?style=flat-square&logo=redis&logoColor=white" alt="redis" />
  <img src="https://img.shields.io/badge/version-0.1.0--prerelease-informational?style=flat-square" alt="version" />
  <img src="https://img.shields.io/badge/license-GPL--3.0-blue?style=flat-square" alt="license: GPL-3.0" />
</p>

[Overview](#-introducing-unboundly) • [Features](#-why-unboundly) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Architecture](#-architecture-overview) • [Persona Config](#-persona-configuration) • [Roadmap](#-roadmap--known-limitations) • [Contributing](#-contributing)

</div>

> **⚠️ Status: Under Active Development**
> This project is **not stable** and is evolving fast. APIs, schemas, and architecture are subject to change without notice — pin your dependencies accordingly.

---

## 🌟 Introducing Unboundly

Meet **Unboundly** — a mood-driven conversational AI framework for simulating realistic, emotionally-aware virtual personas — not scripted bots. Built with **Python**, **FastAPI**, and **Redis**, it powers characters that behave like actual people: they get busy, they get moody, they take time to reply, and they sometimes reach out first.

Unlike typical LLM wrappers that generate an instant, uniform reply to every message, Unboundly layers a **behavioral simulation engine** on top of your favorite language model (local via **LM Studio** or hosted via the **OpenAI API**) to decide *if*, *when*, and *how* a persona responds.

The name says it all: personas here aren't bound to instant replies, aren't bound to a single scripted response, aren't bound by rigid rules. They're **unbound** — free to have a bad day, a curious streak, or a moment of silence, just like a real person would.

### 🧠 The Core Idea: Mood-Driven Behavior

A persona's engagement is shaped by:

- 💓 **Current emotional state** — valence, arousal, irritability, affection, curiosity, fear
- 🕐 **Time of day & availability** — personas aren't online 24/7
- 💬 **Conversation history & events** — every interaction leaves a trace
- 🎨 **Personality traits** — warmth, assertiveness, playfulness, and more
- 🔔 **Self-triggered follow-ups** — personas can initiate messages, not just react

The result: virtual characters with a genuine sense of presence, pacing, and personality — ideal for narrative AI, companion apps, simulation research, and next-gen conversational UX.

---

## ✨ Why Unboundly?

| | |
|---|---|
| 🎭 **Authentic Personas** | Rich JSON-defined characters with configurable traits and biography |
| 🌊 **Dynamic Mood Engine** | Multi-dimensional emotional states that evolve, decay, and react to events |
| ⏳ **Realistic Pacing** | Probabilistic online/busy/reply behavior instead of instant robotic replies |
| 🔌 **Pluggable AI Backend** | Works with local **LM Studio** models or any OpenAI-compatible API |
| ⚡ **Modern Stack** | FastAPI + Redis + SQLite for speed, memory, and persistence |
| 🧩 **Extensible Architecture** | Clean domain-driven layers make it easy to customize behavior logic |

---

## 🛠️ Requirements

| Requirement | Details |
|---|---|
| **Python** | 3.13 or higher |
| **Docker Desktop** | Required on Windows for Redis |
| **AI Provider** | [LM Studio](https://lmstudio.ai/) (recommended, local) *or* OpenAI-compatible API |

---

## 📦 Installation

### 1. Install Python 3.13+
Download from [python.org](https://www.python.org/downloads/) and make sure it's added to your `PATH`.

### 2. Install Docker Desktop
Get it from [Docker's official site](https://www.docker.com/products/docker-desktop) — required for running Redis on Windows.

### 3. Install Project Dependencies

```bash
pip install -e ".[dev]"
```

This pulls in everything defined in `pyproject.toml`, including:

- ⚡ **FastAPI** — web framework
- 🚀 **Uvicorn** — ASGI server
- 🧵 **Redis** — message queue & mood/memory store
- ✅ **Pydantic** — data validation
- 🤖 **OpenAI / LM Studio compatible client**

### 4. Configure Your AI Provider

**Option A — LM Studio (recommended, runs locally):**

```bash
set OPENAI_API_BASE=http://localhost:1234/v1
set OPENAI_API_KEY=not-needed
```

**Option B — OpenAI API:**

```bash
set OPENAI_API_KEY=your-api-key
```

---

## 🚀 Quick Start

### 1. Spin up Redis via Docker

```bash
docker-compose up -d
```

Starts Redis on port `6379` — required for message queuing and persona memory.

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
2. Load the **Bionic** model (or a compatible alternative)
3. Start the local inference server on `http://localhost:1234`

You're live! 🎉

---

## 🏗️ Architecture Overview

Unboundly follows a clean, layered, domain-driven design:

```
User Message
    │
    ▼
Load Persona + Current Mood State
    │
    ▼
Behavior Engine Decision
    ├── Is the persona online?
    ├── Are they busy?
    ├── Will they read the message?
    └── Will they reply?
    │
    ▼
If Reply Decision:
    ├── Generate response via AI provider
    ├── Update mood based on the event
    └── Schedule a possible follow-up (self-triggered)
    │
    ▼
Persist → SQLite (history) · Redis (mood + pub/sub status)
```

### 📁 Layer Breakdown

<details>
<summary><strong>🧱 <code>app/core/</code> — Foundation & Configuration</strong></summary>

- `config.py` — Application settings & environment variables
- `logger.py` — Structured logging across the system
- `models.py` — Database ORM models (`ConversationMessage`, etc.)
- `web.py` — Web server lifecycle & initialization

</details>

<details>
<summary><strong>🧠 <code>app/domain/</code> — Business Logic Engine</strong></summary>

- `behavior.py` — **Behavior Engine**: decides if/when a persona responds, based on mood, availability, and context
- `mood.py` — **Mood State Management**: tracks valence, arousal, irritability, affection, curiosity, and fear, with time-based decay
- `models.py` — Core domain models (`Decision`, `MoodState`, etc.)

</details>

<details>
<summary><strong>🔌 <code>app/infrastructure/</code> — External System Integration</strong></summary>

- `ai.py` — AI provider integration (OpenAI-compatible interface for LM Studio, OpenAI, etc.)
- `redis_store.py` — Redis-based memory & scheduled task management
- `sqlite.py` — SQLite database for conversation persistence
- `persona_store.py` — Persona definition & trait loading

</details>

<details>
<summary><strong>🧭 <code>app/services/</code> — High-Level Orchestration</strong></summary>

- `conversation.py` — Orchestrates the full flow: mood evaluation → behavior decision → message generation → follow-up scheduling

</details>

<details>
<summary><strong>🌐 <code>app/api/</code> & <code>app/web/</code> — User Interfaces</strong></summary>

- `routes.py` — REST API endpoints and web routes for chat interaction

</details>

### 🔑 Key Components

1. **Behavior Engine** (`app/domain/behavior.py`) — Probabilistic decision-making: ignore, read, reply later, or reply now.
2. **Mood System** (`app/domain/mood.py`) — Multi-dimensional emotional state that decays over time and shifts with events.
3. **Worker Task** (`app/main.py`) — Background process handling scheduled replies and self-triggered actions.
4. **AI Integration** (`app/infrastructure/ai.py`) — Pluggable backend generating contextual, mood-aware responses.

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
    "online_probability_by_hour": ["..."],
    "busy_probability": 0.18,
    "read_probability_when_online": 0.86,
    "reply_probability_when_seen": 0.78,
    "delay_seconds": { "min": 4, "max": 900, "median": 45 }
  }
}
```

### 🔍 Key Properties

| Field | Description |
|---|---|
| **`traits`** | Core personality dials, each scored `0.0`–`1.0` |
| **`mood.dimensions`** | Emotional axes that respond to events and decay over time |
| **`availability`** | Hour-by-hour online probability and response timing patterns |
| **`event_weights`** | How specific conversation events shift the persona's mood |

---

## 📂 Project Structure

```
persona/
├── app/
│   ├── core/              # Configuration, logging, database models
│   ├── domain/             # Behavior engine, mood system (business logic)
│   ├── infrastructure/     # AI, Redis, SQLite, persona storage
│   ├── services/           # Conversation orchestration
│   ├── api/                # REST API routes
│   ├── web/                # Web interface routes
│   └── main.py              # FastAPI app entry point
├── personas/               # Persona definition files (JSON)
├── ui/
│   ├── templates/           # HTML templates
│   └── assets/              # CSS, JS, images
├── docker-compose.yml       # Redis and services configuration
├── pyproject.toml           # Python dependencies and project metadata
└── README.md                # This file
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

### Local `.env` Setup

```env
OPENAI_API_BASE=http://localhost:1234/v1
OPENAI_API_KEY=not-needed
REDIS_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./persona.db
LOG_LEVEL=INFO
```

---

## 🗺️ Roadmap & Known Limitations

- [ ] Mood persistence and historical tracking needs refinement
- [ ] Multi-persona conversation dynamics not yet implemented
- [ ] More sophisticated context windowing for longer conversations
- [ ] Enhanced personality trait expression in generated responses
- [ ] Comprehensive test coverage

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

*Last Updated: September 2026 · Version 0.1.0 (Pre-release)*

</div>
