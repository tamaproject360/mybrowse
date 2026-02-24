# Architecture Guidelines — mybrowse

> **For AI code generators:** This document is the single source of truth for the mybrowse project.
> Read it fully before generating or modifying any code.

---

## Project Overview

**mybrowse** is a multi-agent AI system that lets users control autonomous browser agents and AI assistants through any messaging channel (Telegram, WhatsApp, CLI, etc.). The system understands natural language tasks, automatically routes them to the most appropriate agent, executes them, and returns results with full persistence via PostgreSQL.

**Core philosophy:**
- Users send plain text — no commands or prefixes required
- The Supervisor (LLM orchestrator) decides which agent handles the task
- All agents are plug-and-play; new ones can be added with minimal boilerplate
- All task history, steps, attachments, and long-term memory are persisted to PostgreSQL
- Channels are also plug-and-play; adding a new channel only requires extending `BaseChannel`

**Current state:** Operational with Telegram channel, three agents (Browser, Chat, Memory), PostgreSQL persistence, and long-term memory injection.

---

## Feature Apps

| Feature | Description | Status |
|---|---|---|
| **Natural language task input** | Users send plain messages — no `/task` prefix needed | ✅ |
| **Automatic agent routing** | LLM picks browser / chat / memory agent based on task semantics | ✅ |
| **Browser automation** | Autonomous web browsing via browser-use (navigate, click, fill, extract, screenshot) | ✅ |
| **Chat / Reasoning** | Direct LLM Q&A, explanation, writing, calculation without opening a browser | ✅ |
| **Long-term memory** | Task results auto-saved to DB; injected as context into future tasks | ✅ |
| **Memory management** | User can explicitly save, list, and delete memories | ✅ |
| **Telegram channel** | Full Telegram bot with live progress updates, inline keyboard, typing indicator | ✅ |
| **Task history** | All tasks persisted with status, output, duration, agent used | ✅ |
| **Screenshot delivery** | Screenshots taken during browsing are sent as images to Telegram | ✅ |
| **File attachments** | Any file produced by the agent is forwarded to the channel | ✅ |
| **Task cancellation** | User can cancel a running task mid-execution | ✅ |
| **CLI mode** | Run single tasks directly from the terminal | ✅ |
| **WhatsApp channel** | Planned — extend `BaseChannel` | 🔜 |
| **Discord channel** | Planned — extend `BaseChannel` | 🔜 |
| **Code execution agent** | Planned — `CodeAgent` for running Python/JS | 🔜 |
| **Search agent** | Planned — dedicated web search + summarize without full browser | 🔜 |

---

## Tech Stack

| Layer | Technology | Version | Notes |
|---|---|---|---|
| **Language** | Python | 3.12 | Required. Use `uv` for env management |
| **Package manager** | uv | latest | Always `uv sync`, never `pip install` |
| **Browser automation** | browser-use | latest | Core library — do NOT modify files inside `browser_use/` |
| **LLM client** | openai (AsyncOpenAI) | latest | Used for routing + ChatAgent; compatible with any OpenAI-compatible endpoint |
| **LLM framework** | browser_use.llm.ChatOpenAI | — | Wraps openai for the browser Agent |
| **Database** | PostgreSQL | 17.6 | Local: `localhost:5432`, DB: `mybrowse` |
| **DB driver** | asyncpg | latest | Async PostgreSQL driver; NO ORM |
| **HTTP client** | aiohttp | latest | Used by TelegramChannel for Telegram Bot API |
| **Async runtime** | asyncio | stdlib | All agent execution is async |
| **Config** | python-dotenv | latest | `.env` file, loaded at startup |
| **Linting/format** | pre-commit | — | Run before every PR |
| **Chrome** | Google Chrome | any | Path: `C:/Program Files/Google/Chrome/Application/chrome.exe` |

**LLM endpoint (local dev):**
- Base URL: `http://localhost:9999/v1`
- API Key: set in `.env` as `OPENAI_API_KEY`
- Model: set in `.env` as `OPENAI_MODEL` (default: `gpt-4o`)

---

## Folder & File Project Structure

```
mybrowse/
│
├── run.py                          # Entry point: CLI mode & Telegram mode
├── db.py                           # Database layer (asyncpg, no ORM)
├── .env                            # Environment config (never commit)
├── .env.example                    # Template for .env
├── pyproject.toml                  # Project dependencies (uv)
│
├── agents/                         # Multi-agent system
│   ├── __init__.py                 # Exports: Supervisor, BaseAgent, AgentContext, AgentResult
│   ├── base.py                     # Shared types: AgentContext, AgentResult, BaseAgent, BrowserConfig
│   ├── supervisor.py               # Supervisor: LLM routing + DB lifecycle + memory
│   ├── browser.py                  # BrowserAgent: browser-use autonomous web browsing
│   ├── chat.py                     # ChatAgent: direct LLM Q&A without browser
│   └── memory.py                   # MemoryAgent: explicit memory CRUD operations
│
├── channels/                       # Channel layer (plug & play)
│   ├── __init__.py                 # Exports: BaseChannel, TelegramChannel
│   ├── base.py                     # BaseChannel: thin wrapper around Supervisor.run()
│   └── telegram/
│       ├── __init__.py
│       └── channel.py              # TelegramChannel: polling, progress, keyboard, file delivery
│
├── screenshots/                    # Auto-created: browser screenshots saved here
│
├── prisma/
│   └── schema.prisma               # DB schema documentation (not used as ORM)
│
├── browser_use/                    # browser-use library (DO NOT MODIFY)
│   ├── agent/                      # Agent service, views, history
│   ├── browser/                    # BrowserSession, BrowserProfile
│   ├── tools/                      # Default tool registry (navigate, click, screenshot, etc.)
│   └── ...
│
└── docs/
    └── specs.md                    # This file
```

**Key invariants:**
- `browser_use/` is a vendored library — never edit files inside it
- `db.py` is a flat module at root — all DB operations go here
- Each agent is a self-contained file in `agents/`
- Each channel is a folder in `channels/`

---

## Workflow

### Full Request Flow

```mermaid
flowchart TD
    User(["👤 User"]) -->|"plain text message"| CH

    subgraph CH["Channel Layer"]
        TG["TelegramChannel\nchannel.py"]
        CLI["CLI\nrun.py"]
    end

    CH -->|"handle_message(task, channel, channel_id, username, on_update)"| BASE["BaseChannel\nchannels/base.py"]
    BASE -->|"Supervisor.run(AgentContext)"| SUP

    subgraph SUP["Supervisor — agents/supervisor.py"]
        direction TB
        S1["1. Fetch memory from DB"] --> S2
        S2["2. task_create in DB"] --> S3
        S3["3. LLM routing\n(JSON response)"] --> S4
        S4{"Which agent?"}
    end

    S4 -->|"browser"| BA
    S4 -->|"chat"| CA
    S4 -->|"memory"| MA

    subgraph AGENTS["Agent Layer"]
        BA["🌐 BrowserAgent\nbrowser.py"]
        CA["💬 ChatAgent\nchat.py"]
        MA["🧠 MemoryAgent\nmemory.py"]
    end

    BA -->|"browser-use Agent.run()"| BU["browser_use\nAgent"]
    CA -->|"AsyncOpenAI chat.completions"| LLM["LLM Endpoint\nlocalhost:9999/v1"]
    MA -->|"db.memory_add/get/delete"| DB

    AGENTS -->|"AgentResult"| SUP2

    subgraph SUP2["Supervisor post-process"]
        P1["task_done in DB"] --> P2
        P2["step_log in DB"] --> P3
        P3["attachment_save in DB"] --> P4
        P4["memory_add (auto-save result)"]
    end

    SUP2 -->|"SupervisorResult"| BASE
    BASE -->|"result + attachments"| CH
    CH -->|"text + images"| User

    BU -->|"AgentHistoryList"| BA
    BU -.->|"on_update callback\n(live step updates)"| CH

    DB[("PostgreSQL\nmybrowse")]
    SUP --> DB
    SUP2 --> DB
```

### Task Routing Decision

```mermaid
flowchart LR
    T["User task text"] --> LLM["LLM Router\ntemp=0.0, JSON mode"]
    LLM --> D{"agent field\nin JSON response"}
    D -->|"browser"| B["🌐 BrowserAgent\nweb navigation, scraping,\nscreenshot, form filling"]
    D -->|"chat"| C["💬 ChatAgent\nQ&A, writing, calculation,\nexplanation, summary"]
    D -->|"memory"| M["🧠 MemoryAgent\nsave / recall / delete\nuser preferences & notes"]
    D -->|"unknown / error"| C
```

### Adding a New Agent

```mermaid
flowchart LR
    A["1. Create\nagents/myagent.py\nextends BaseAgent"] --> B
    B["2. Define\nname, description\nrun(ctx) method"] --> C
    C["3. Register\nsupervisor.register_agent(MyAgent(llm))"] --> D
    D["Supervisor LLM\nautomatically routes\ntasks to new agent"]
```

### Adding a New Channel

```mermaid
flowchart LR
    A["1. Create\nchannels/mychannel/\nchannel.py"] --> B
    B["2. Extend BaseChannel\nimplement start() stop()"] --> C
    C["3. Call\nself.handle_message(\ntask, channel, channel_id,\nusername, on_update)"] --> D
    D["4. Display\nSupervisorResult\nto user"]
```

---

## Data Interaction

### Database Schema

```mermaid
erDiagram
    tasks {
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
        varchar channel
        varchar channel_id
        varchar username
        text prompt
        varchar status
        text output
        boolean success
        int steps
        int duration_ms
    }

    step_logs {
        uuid id PK
        uuid task_id FK
        timestamptz created_at
        int step_num
        text[] actions
        text next_goal
        text evaluation
        varchar url
    }

    attachments {
        uuid id PK
        uuid task_id FK
        timestamptz created_at
        varchar file_name
        text file_path
        varchar file_type
        varchar mime_type
        int size_bytes
        boolean sent_to_channel
    }

    memories {
        uuid id PK
        timestamptz created_at
        varchar channel
        varchar channel_id
        varchar username
        text content
        varchar mem_type
        varchar source
        uuid task_id FK
    }

    tasks ||--o{ step_logs : "has"
    tasks ||--o{ attachments : "produces"
    tasks ||--o{ memories : "generates"
```

### Task Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: task_create()
    PENDING --> RUNNING: task_start() on step 1
    RUNNING --> DONE: task_done(success=True)
    RUNNING --> FAILED: task_done(success=False)
    RUNNING --> CANCELLED: task_cancel() on user /cancel
    DONE --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

### Memory Lifecycle

```mermaid
sequenceDiagram
    participant U as User
    participant SUP as Supervisor
    participant DB as PostgreSQL

    U->>SUP: New task
    SUP->>DB: memory_format_for_prompt(channel, channel_id)
    DB-->>SUP: formatted memory string
    SUP->>SUP: inject memory into AgentContext
    SUP->>SUP: run agent with enriched context
    SUP->>DB: memory_add(task_result) — auto on success
    SUP-->>U: Result

    U->>SUP: "ingat bahwa..." (explicit)
    SUP->>SUP: route → MemoryAgent
    SUP->>DB: memory_add(user_note)
    SUP-->>U: "Tersimpan"

    U->>SUP: /memory
    SUP->>DB: memory_get_context()
    DB-->>SUP: list[MemoryRecord]
    SUP-->>U: formatted memory list

    U->>SUP: /forget
    SUP->>DB: memory_delete(channel, channel_id)
    SUP-->>U: "N memory dihapus"
```

### DB Function Reference

| Function | Description |
|---|---|
| `task_create(channel, channel_id, prompt, username)` | Create PENDING task, return UUID |
| `task_start(task_id)` | Set status → RUNNING |
| `task_done(task_id, output, success, steps, duration_ms)` | Set status → DONE/FAILED |
| `task_cancel(task_id)` | Set status → CANCELLED |
| `task_get(task_id)` | Fetch one TaskRecord |
| `task_list(channel, channel_id, limit)` | Fetch recent tasks |
| `step_log(task_id, step_num, actions, next_goal, evaluation, url)` | Log one agent step |
| `attachment_save(task_id, file_name, file_path, file_type, mime_type, size_bytes)` | Record a file |
| `attachment_mark_sent(attachment_id)` | Mark file as sent to channel |
| `memory_add(channel, channel_id, content, mem_type, username, task_id, source)` | Save a memory |
| `memory_get_context(channel, channel_id, limit)` | Get recent memories |
| `memory_format_for_prompt(channel, channel_id, limit)` | Get formatted memory string for LLM prompt |
| `memory_delete(channel, channel_id)` | Delete all memories for a chat, return count |

---

## Commands

### Development Setup

```bash
# Create virtual environment (Python 3.12 required)
uv venv --python 3.12
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # Linux/macOS

# Install all dependencies
uv sync

# Copy and fill environment config
cp .env.example .env
# Edit .env with your values
```

### Running the App

```bash
# Telegram bot mode (main production mode)
PYTHONUTF8=1 .venv/Scripts/python.exe run.py --telegram

# CLI mode — run a single task
PYTHONUTF8=1 .venv/Scripts/python.exe run.py "cari harga iPhone 16 di tokopedia"

# CLI mode — default task (google.com title)
PYTHONUTF8=1 .venv/Scripts/python.exe run.py
```

> **Windows requirement:** Always set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` to avoid emoji/Unicode issues in cp1252 terminals. Both are set automatically inside `run.py`.

### Database

```bash
# Connect to PostgreSQL
psql -U postgres -d mybrowse

# Create schema (run once)
# See prisma/schema.prisma for table definitions
# Tables: tasks, step_logs, attachments, memories
```

### Code Quality

```bash
# Run pre-commit hooks (formatter + linter) — REQUIRED before PR
./bin/lint.sh

# Run tests
./bin/test.sh

# Full setup from scratch
./bin/setup.sh
```

### Environment Variables

```bash
# Required
OPENAI_API_KEY=                    # LLM API key
OPENAI_BASE_URL=http://localhost:9999/v1   # LLM base URL (OpenAI-compatible)
OPENAI_MODEL=gpt-4o                # Model name
TELEGRAM_BOT_TOKEN=                # From @BotFather

# Optional
TELEGRAM_ALLOWED_USERS=123,456     # Comma-separated chat IDs (empty = allow all)
CHROME_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
AGENT_HEADLESS=false               # true = headless browser (no window)
AGENT_MAX_STEPS=50                 # Max steps per browser task
DATABASE_URL=postgresql://postgres:password@localhost:5432/mybrowse

# Telemetry (opt out)
ANONYMIZED_TELEMETRY=false
```

---

## Code Style

### Components

Each component is a **self-contained Python file** in its own layer:

- **Agent** = `agents/<name>.py`, extends `BaseAgent`, implements `run(ctx) -> AgentResult`
- **Channel** = `channels/<name>/channel.py`, extends `BaseChannel`, implements `start()` + `stop()`
- **DB function** = flat async function in `db.py`, uses `async with db() as conn:`
- **Config dataclass** = `@dataclass` at top of relevant module (`BrowserConfig`, `AgentContext`, etc.)

### Imports

Ordering (enforced by pre-commit):
```python
# 1. stdlib
import asyncio
import logging
import os

# 2. third-party
from openai import AsyncOpenAI
import asyncpg

# 3. internal — absolute imports only
import db
from agents.base import AgentContext, AgentResult, BaseAgent
from agents.supervisor import Supervisor, SupervisorResult
from channels.base import BaseChannel
```

Rules:
- Always use **absolute imports** (never relative `from . import`)
- Import `db` as a module, not individual functions: `import db` then `await db.task_create(...)`
- Never import from inside `browser_use/` except: `browser_use.agent.service.Agent`, `browser_use.browser.*`, `browser_use.llm.*`, `browser_use.Tools`

### Naming Conventions

| Item | Convention | Example |
|---|---|---|
| Files | `snake_case.py` | `browser.py`, `channel.py` |
| Classes | `PascalCase` | `BrowserAgent`, `TelegramChannel` |
| Functions / methods | `snake_case` | `run()`, `task_create()`, `handle_message()` |
| Async functions | `snake_case` (always `async def`) | `async def run(ctx)` |
| Constants | `UPPER_SNAKE_CASE` | `ROUTER_SYSTEM`, `TELEGRAM_API` |
| Dataclass fields | `snake_case` | `task_id`, `channel_id`, `agent_used` |
| Agent names | lowercase single word | `'browser'`, `'chat'`, `'memory'` |
| Channel names | lowercase single word | `'telegram'`, `'whatsapp'`, `'cli'` |
| DB table names | `snake_case` plural | `tasks`, `step_logs`, `attachments`, `memories` |
| Task status | `UPPER_CASE` | `PENDING`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED` |
| Memory types | `snake_case` | `task_result`, `user_note`, `general` |

### Error Handling

**Golden rule: DB and non-critical operations must never crash the main flow.**

```python
# ✅ CORRECT — non-fatal DB call
try:
    task_id = await db.task_create(...)
except Exception as e:
    logger.warning(f'DB task_create gagal: {e}')
    task_id = None

# ✅ CORRECT — non-fatal callback
try:
    await ctx.on_update('Status update...')
except Exception:
    pass

# ✅ CORRECT — agent error returns AgentResult, never raises
try:
    history = await agent.run(max_steps=...)
    ...
    return AgentResult(success=True, output=...)
except asyncio.CancelledError:
    raise  # ALWAYS re-raise CancelledError — it's used for task cancellation
except Exception as e:
    logger.exception(f'AgentName error: {e}')
    return AgentResult(success=False, output='', errors=[str(e)])

# ❌ WRONG — raises to caller
await db.task_create(...)  # can crash entire bot if DB is down
```

**`asyncio.CancelledError` must always be re-raised** — it is used for user-initiated task cancellation via `/cancel`.

**Logging levels:**
- `logger.debug(...)` — step-by-step trace, non-critical failures, callback errors
- `logger.info(...)` — routing decisions, task start/done, bot lifecycle
- `logger.warning(...)` — recoverable failures (DB down, routing fallback)
- `logger.exception(...)` — unexpected agent errors (includes traceback)

---

## UI Components

mybrowse has no web UI. The "UI" is the Telegram Bot interface. Below are the components:

### Progress Message (edit-in-place)

Sent when a task starts, edited every ≥2 seconds with live status:

```
⏳ Memproses...
`coba cek harga iphone termurah di tokopedia`

Menganalisis task...
```

→ Transitions to:

```
🌐 Agent Berjalan
`coba cek harga iphone termurah di tokopedia`

Waktu: 12s [████████░░░░░░░░]
Status: [browser] step 3: navigate → Membuka halaman produk...
```

→ Transitions to done:

```
✅ Selesai
`coba cek harga iphone termurah di tokopedia`

Agent: 🌐 browser
Waktu: 45s
```

### Result Message

Sent after task completes:
```
✅ Hasil

iPhone 16 termurah di Tokopedia:
- iPhone 16 128GB: Rp 12.999.000
- iPhone 16 256GB: Rp 14.499.000
...
```

Followed by any screenshot images as Telegram photos.

### Inline Keyboards

**Main keyboard** (shown with /start, /help, /status, /memory):
```
[ 📊 Status ]  [ ❓ Help  ]
[ 🚫 Cancel ]  [ 🧠 Memory]
```

**Running keyboard** (shown on progress message):
```
[ 🚫 Cancel Task ]
```

**Done keyboard** (shown after result):
```
[ 📊 Status ]  [ 🧠 Memory ]
```

### Bot Commands (registered via setMyCommands)

| Command | Description |
|---|---|
| `/start` | Menu utama + welcome message |
| `/status` | Status bot & task yang sedang berjalan |
| `/cancel` | Batalkan task yang sedang berjalan |
| `/history` | Riwayat 5 task terakhir dari DB |
| `/memory` | Tampilkan memory tersimpan |
| `/forget` | Hapus semua memory untuk chat ini |
| `/help` | Panduan penggunaan lengkap |

**Plain text messages** (no `/` prefix) are sent directly to the Supervisor — no `/task` command needed.  
`/task <text>` is supported for backward compatibility.

### Agent Icons (Telegram display)

| Agent | Icon | When shown |
|---|---|---|
| `browser` | 🌐 | Web browsing tasks |
| `chat` | 💬 | Q&A, reasoning |
| `memory` | 🧠 | Memory operations |
| fallback | ⚡ | Unknown agent |

---

## Accessibility

Since this is a CLI/chatbot application (no graphical UI), accessibility guidelines apply to message formatting:

### Message Clarity
- Every response includes a status indicator (`✅` / `❌`) at the start
- Agent type is always shown so users understand what was used
- Elapsed time always shown so users know how long tasks took
- Long outputs are truncated at 3800 chars with `[output dipotong]` notice

### Error Messages
- Errors are shown in `<code>` blocks in Telegram (monospace, easy to copy)
- Error messages are in the same language as the user's message
- Non-fatal errors (DB down) are silently handled — user never sees infrastructure errors

### Progressive Disclosure
- Initial response is immediate (progress message sent within milliseconds)
- Live updates every ≥2 seconds show what the agent is doing
- Final result is a separate message from the progress message
- Screenshots are sent after the text result (not before)

### Rate Limiting & Flood Protection
- Progress message edits are throttled to max once per 2 seconds (`progress_edit_interval`)
- Photo batch sends have 0.3s delay between each image
- Typing indicator loops every 4 seconds (Telegram's activity window)
- Browser tasks use `asyncio.Lock()` — only one browser instance runs per Supervisor

### Language
- Bot responses are in Bahasa Indonesia by default
- Agent responses (especially ChatAgent) mirror the user's language
- System prompt instructs ChatAgent: "Jawab dengan bahasa yang sama dengan pertanyaan user"
- Log messages are in Indonesian (matching the codebase language)

---

*Generated from codebase state as of 2026-02-25. Update this file when adding new agents, channels, or database tables.*
