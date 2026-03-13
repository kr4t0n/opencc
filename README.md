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

## Slack App Setup

Create a new Slack app at [api.slack.com/apps](https://api.slack.com/apps) and configure the following permissions and features.

### Required Bot Token Scopes

These scopes must be added under **OAuth & Permissions → Bot Token Scopes**:

| Scope | Why it's needed |
|---|---|
| `app_mentions:read` | Allows the bot to receive `app_mention` events when a user mentions it in a channel or thread |
| `chat:write` | Allows the bot to post Claude's responses back to channels and threads via `chat_postMessage` |
| `files:read` | Allows the bot to download image attachments that users include in their messages, so they can be forwarded to Claude |

### Event Subscriptions

Enable **Event Subscriptions** and subscribe to the following bot events:

| Event | Description |
|---|---|
| `app_mention` | Fires when a user mentions the bot (e.g. `@opencc fix this bug`). This is the only event the gateway listens to. |

### Socket Mode

opencc uses **Socket Mode** so the bot communicates over WebSocket — no public URL or ingress is required.

1. Go to **Settings → Socket Mode** and enable it.
2. Generate an **App-Level Token** with the `connections:write` scope. This produces a token starting with `xapp-` that you will set as `SLACK_APP_TOKEN`.

### Tokens

After installing the app to your workspace you will need two tokens:

| Token | Format | Where to find it |
|---|---|---|
| **Bot Token** | `xoxb-...` | **OAuth & Permissions → Bot User OAuth Token** |
| **App-Level Token** | `xapp-...` | **Settings → Basic Information → App-Level Tokens** |

### Install the App

1. Go to **Install App** and click **Install to Workspace**.
2. Invite the bot to any channels where you want to use it (e.g. `/invite @opencc`).

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
   | `CLAUDE_CLI_ARGS` | No | Base arguments passed to the Claude Code CLI (default: `-p --output-format json --dangerously-skip-permissions`) |
   | `CLAUDE_EXTRA_ARGS` | No | Extra arguments appended to the CLI invocation (default: empty). Example: `--model sonnet --verbose` |

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

## Slash Commands

Messages that start with `/` are handled as gateway commands instead of being forwarded to Claude Code.

| Command | Description |
|---|---|
| `/help` | Show the list of available commands |
| `/stop` | Cancel the currently running Claude response |
| `/btw <message>` | Send a quick, context-free reply without session history |

## Adding New Adapters

Subclass `IMAdapter` in `src/opencc/adapters/base.py` and implement `start`, `stop`, and `send_message`. Register the adapter in `src/opencc/main.py` alongside the existing `SlackAdapter`.

## License

MIT
