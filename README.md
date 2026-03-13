# opencc

IM-to-Claude Code gateway — bridge Slack (and other IM apps) to Claude Code CLI sessions.

## Overview

opencc is a lightweight FastAPI service that connects instant-messaging platforms to [Claude Code](https://docs.anthropic.com/en/docs/claude-code). When someone mentions the bot in Slack, opencc forwards the message to a Claude Code CLI session and posts the response back in the thread. Each channel/thread gets its own persistent session, so conversations maintain context.

## Architecture

```
Slack ──► SlackAdapter ──► GatewayRouter ──► ClaudeProcessManager ──► Claude Code CLI
                                                   │
                                              ClaudeSession
                                           (per channel/thread)
```

- **Adapters** (`src/opencc/adapters/`) — Platform-specific connectors. The `IMAdapter` base class defines the interface; `SlackAdapter` implements it using Socket Mode (WebSocket, no public URL required).
- **Gateway Router** (`src/opencc/gateway/`) — Routes incoming messages to the correct Claude Code session based on adapter, channel, and thread.
- **Claude Process Manager** (`src/opencc/claude/`) — Manages Claude Code CLI subprocesses. Each session key maps to a `ClaudeSession` that tracks the conversation's `session_id` for `--resume` support.

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Setup

1. **Install dependencies**

   ```bash
   uv sync
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and fill in your Slack credentials:

   | Variable | Required | Description |
   |---|---|---|
   | `SLACK_BOT_TOKEN` | Yes | Slack bot token (`xoxb-...`) |
   | `SLACK_APP_TOKEN` | Yes | Slack app-level token (`xapp-...`) |
   | `CLAUDE_CLI_PATH` | No | Path to the Claude Code CLI binary (default: `claude`) |
   | `CLAUDE_WORK_DIR` | No | Working directory for Claude Code sessions (default: `.`) |

3. **Run the gateway**

   ```bash
   uv run opencc
   ```

   The server starts on `0.0.0.0:8000` by default.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/sessions` | List active Claude Code sessions |

## Adding New Adapters

Subclass `IMAdapter` in `src/opencc/adapters/base.py` and implement `start`, `stop`, and `send_message`. Register the adapter in `src/opencc/main.py` alongside the existing `SlackAdapter`.

## License

MIT
