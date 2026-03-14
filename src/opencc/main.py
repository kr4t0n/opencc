from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from opencc.adapters.slack import SlackAdapter
from opencc.claude.process import ClaudeProcessManager
from opencc.claude.store import SqliteSessionStore
from opencc.config import get_settings
from opencc.gateway.router import GatewayRouter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    session_store = SqliteSessionStore(settings.session_store_path)
    claude_manager = ClaudeProcessManager(
        cli_path=settings.claude_cli_path,
        work_dir=settings.claude_work_dir,
        cli_args=settings.claude_cli_args,
        extra_args=settings.claude_extra_args,
        session_store=session_store,
    )
    slack_adapter = SlackAdapter(
        bot_token=settings.slack_bot_token,
        app_token=settings.slack_app_token,
    )
    router = GatewayRouter(claude_manager, adapter=slack_adapter)

    app.state.claude_manager = claude_manager
    app.state.router = router
    app.state.slack_adapter = slack_adapter

    await slack_adapter.start(router.handle)
    logger.info("opencc gateway is up")

    yield

    await slack_adapter.stop()
    await claude_manager.cleanup()
    logger.info("opencc gateway shut down")


app = FastAPI(title="opencc", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/sessions")
async def sessions() -> list[dict]:
    return app.state.claude_manager.list_sessions()


def cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    uvicorn.run(
        "opencc.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    cli()
