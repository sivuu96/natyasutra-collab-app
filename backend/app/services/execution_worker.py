"""Background loop that drains the execution outbox.

Runs inside the web process (the hub is a single instance). Each pass runs in a worker thread, so the
blocking database and provider calls never stall the event loop.
"""

import asyncio
import contextlib
import logging
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.services.execution import ExecutionProvider, process_once
from app.services.sweeper import sweep_runs

log = logging.getLogger("collab.execution_worker")


async def run_outbox_worker(
    session_factory: sessionmaker[Session],
    provider: ExecutionProvider,
    *,
    interval_seconds: float,
    stop: asyncio.Event,
    owner: str | None = None,
) -> None:
    worker_owner = owner or f"hub-{uuid.uuid4().hex[:8]}"
    while not stop.is_set():
        try:
            processed = await asyncio.to_thread(process_once, session_factory, provider, owner=worker_owner)
            if processed:
                log.info("execution outbox processed", extra={"count": processed})
            swept = await asyncio.to_thread(sweep_runs, session_factory, provider)
            if swept:
                log.info("stuck runs handled", extra={"count": swept})
        except Exception:  # keep the loop alive; the error is logged with its traceback
            log.exception("execution outbox pass failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
