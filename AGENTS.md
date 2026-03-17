# opencc — AI Agent Reference

This document is the canonical reference for AI agents working on this codebase.

---

## Rules

After making any code changes, always read `README.md` and check whether the change affects documented behavior (e.g. new/changed environment variables, commands, endpoints, architecture, setup steps). If it does, update the README accordingly in the same changeset.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Project Layout](#project-layout)
3. [Architecture](#architecture)
4. [Data Model](#data-model)
5. [Slash Commands](#slash-commands)
6. [Environment Variables](#environment-variables)

---

## Project Overview

**opencc** is a lightweight FastAPI service that bridges instant messaging platforms (currently Slack) to Claude Code CLI sessions. When users mention the bot in Slack, the message is forwarded to a Claude Code CLI process and the response is posted back. Each channel/thread maintains its own persistent session with conversation context via Claude's `--resume` flag.

- **Language**: Python 3.10+
- **Framework**: FastAPI + Uvicorn (ASGI)
- **IM Platform**: Slack Bolt (async) via Socket Mode
- **Async**: asyncio subprocess management
- **Configuration**: Pydantic Settings + python-dotenv
- **Package Manager**: uv

---

## Project Layout

```
opencc/
├── .env.example              # environment variable template
├── pyproject.toml             # package metadata and dependencies
├── uv.lock                   # uv lock file
├── README.md                 # project documentation
├── AGENTS.md                 # this file
└── src/opencc/
    ├── __init__.py
    ├── main.py               # FastAPI app entrypoint, lifespan setup, Uvicorn startup
    ├── config.py              # Pydantic Settings (loads .env)
    ├── adapters/
    │   ├── base.py            # IMAdapter ABC, Message dataclass, MessageHandler type
    │   └── slack.py           # SlackAdapter — Socket Mode, app_mention listener, image download
    ├── claude/
    │   ├── process.py         # ClaudeSession (per-conversation) and ClaudeProcessManager
    │   └── store.py           # SessionStore ABC and SqliteSessionStore
    └── gateway/
        └── router.py          # GatewayRouter — message routing, slash commands, image handling
```

---

## Architecture

### Message Flow

```
Slack ──► SlackAdapter ──► GatewayRouter ──► ClaudeProcessManager ──► Claude Code CLI
               ▲                │                    │                  (stream-json)
               │           (post/update)        ClaudeSession
               └────────── live updates ◄──── (per channel/thread)
```

Responses are streamed using Claude Code's `stream-json` output format. The router posts an initial status message to Slack, then updates it in real-time as tool-use events arrive. Once the CLI finishes, the message is updated with the tool log and final result.

### Components

| Component | Location | Responsibility |
|---|---|---|
| **IMAdapter** | `adapters/base.py` | Abstract base class for IM platform connectors. Provides `truncate()` helper that trims oversized text from the beginning (keeping recent content visible), controlled by `max_message_length` |
| **SlackAdapter** | `adapters/slack.py` | Slack Socket Mode integration — listens for `app_mention` events, downloads image attachments to temp files, splits long responses into 3000-char chunks. Provides `post_message` / `update_message` for live streaming updates |
| **GatewayRouter** | `gateway/router.py` | Routes messages to per-session Claude processes. Session key: `{adapter_name}:{channel_id}:{thread_id}`. Streams responses via `post_message` / `update_message` on the adapter, showing tool usage in real-time. Handles slash commands and image prompt assembly |
| **ClaudeSession** | `claude/process.py` | Tracks a single Claude Code conversation. Spawns CLI as async subprocess with `--resume` for persistent context. Supports both batch (`send`) and streaming (`send_streaming`) modes. Locks to one message at a time |
| **ClaudeProcessManager** | `claude/process.py` | Manages multiple `ClaudeSession` instances keyed by session key. Handles `/stop` and `/sessions`. Accepts an optional `SessionStore` for persistence and rehydrates sessions on startup |
| **SessionStore** | `claude/store.py` | ABC for persisting session_key → session_id mappings. `SqliteSessionStore` is the default implementation |
| **Settings** | `config.py` | Pydantic `BaseSettings` singleton (via `lru_cache`) loading from `.env` |
| **main.py** | `main.py` | FastAPI app with lifespan context manager. Initializes adapter, router, and process manager on startup. Exposes `/health` and `/sessions` HTTP endpoints |

### CLI Invocation

Each `ClaudeSession` spawns Claude Code CLI with these flags:

- `-p` — prose mode
- `--output-format json` — structured output (batch mode) or `--output-format stream-json` (streaming mode, overridden automatically by `send_streaming`)
- `--dangerously-skip-permissions` — non-interactive execution
- `--resume <session_id>` — persistent conversation context (after first message)

In streaming mode, the output format is automatically overridden to `stream-json` regardless of the configured `CLAUDE_CLI_ARGS`.

### Extensibility

New IM platforms can be added by subclassing `IMAdapter` in `adapters/` and registering the adapter in `main.py`. The adapter must implement `start()`, `stop()`, and call the registered `MessageHandler` callback on incoming messages.

---

## Data Model

Session mappings (session_key → session_id) are persisted to a SQLite file via `SessionStore`. All other state (locks, subprocess handles) is in-memory and transient.

### Message

```python
@dataclass
class Message:
    adapter_name: str          # e.g. "slack"
    channel_id: str            # IM platform channel identifier
    thread_id: str             # thread within the channel
    user_id: str               # sender identifier
    text: str                  # message content
    raw: dict                  # original event payload
    images: list[str]          # local temp file paths for image attachments
```

### ClaudeSession

```python
@dataclass
class ClaudeSession:
    session_key: str           # "{adapter}:{channel}:{thread}"
    session_id: Optional[str]  # Claude --resume ID (set after first response)
    _lock: asyncio.Lock        # concurrency guard (one message at a time)
    _proc: Optional[Process]   # active subprocess handle
```

### Settings

```python
class Settings(BaseSettings):
    slack_bot_token: str       # xoxb-...
    slack_app_token: str       # xapp-...
    claude_cli_path: str       # default: "claude"
    claude_work_dir: str       # default: "."
    session_store_path: str    # default: "sessions.db"
    host: str                  # default: "0.0.0.0"
    port: int                  # default: 8000
```

---

## Slash Commands

Commands recognized by `GatewayRouter` when a message starts with `/`:

| Command | Description |
|---|---|
| `/help` | Lists available commands |
| `/stop` | Terminates the current session's Claude process and removes the session |
| `/sessions` | Lists all active Claude Code sessions |
| `/ask <message>` | Sends a quick, context-free reply using an ephemeral session (no session persistence) |
| `/btw <message>` | Sends a side message in a cloned session (original conversation is preserved) |

Leading slashes in non-command messages are escaped to prevent Claude Code from interpreting them as skill invocations.

---

## Environment Variables

All loaded via Pydantic Settings from a `.env` file in the project root.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | — | Slack bot OAuth token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | — | Slack app-level token (`xapp-...`) for Socket Mode |
| `CLAUDE_CLI_PATH` | No | `claude` | Absolute path to the Claude Code CLI binary |
| `CLAUDE_WORK_DIR` | No | `.` | Working directory for Claude Code sessions |
| `SESSION_STORE_PATH` | No | `sessions.db` | Path to the SQLite file for persisting session mappings |
| `HOST` | No | `0.0.0.0` | FastAPI server bind address |
| `PORT` | No | `8000` | FastAPI server listen port |
