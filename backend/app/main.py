"""FastAPI application factory."""

import asyncio
import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api import activity, agents, auth, bridge, devices, discussions, realtime, sessions, work, workspace
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimiter
from app.db.session import make_engine, make_session_factory
from app.deps import CSRF_COOKIE, CSRF_HEADER
from app.realtime.hub import hub
from app.services.execution_worker import run_outbox_worker
from app.services.providers import build_execution_provider

logger = logging.getLogger("app")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if not settings.bridge_token_secret:
        # Development convenience: tokens stay valid for this process only. Set BRIDGE_TOKEN_SECRET in production.
        settings.bridge_token_secret = secrets.token_urlsafe(48)
        logger.warning("BRIDGE_TOKEN_SECRET not set; generated an ephemeral secret for this process")
    db_engine = engine or make_engine(settings.database_url)

    execution_provider = build_execution_provider(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.bind_loop(asyncio.get_running_loop())
        logger.info("app starting", extra={"environment": settings.environment})
        worker_stop = asyncio.Event()
        worker: asyncio.Task[None] | None = None
        if execution_provider is not None and settings.execution_worker_enabled:
            worker = asyncio.create_task(
                run_outbox_worker(
                    app.state.session_factory,
                    execution_provider,
                    interval_seconds=settings.execution_worker_interval_seconds,
                    stop=worker_stop,
                )
            )
        yield
        worker_stop.set()
        if worker is not None:
            await worker
        db_engine.dispose()
        logger.info("app stopped")

    app = FastAPI(title="NatyaSutra Collaboration Hub", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = db_engine
    app.state.session_factory = make_session_factory(db_engine)
    app.state.rate_limiter = RateLimiter()
    app.state.execution_mode = settings.execution_provider
    app.state.execution_provider = execution_provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        # Double-submit CSRF: a state-changing API call must echo the ns_csrf cookie in a header.
        # Bridge endpoints are exempt: bridges are non-browser clients that never send cookies.
        if (
            request.url.path.startswith("/api/")
            and not request.url.path.startswith("/api/v1/bridge/")
            and request.method in UNSAFE_METHODS
        ):
            cookie_value = request.cookies.get(CSRF_COOKIE, "")
            header_value = request.headers.get(CSRF_HEADER, "")
            if not cookie_value or not secrets.compare_digest(cookie_value, header_value):
                return JSONResponse(status_code=403, content={"detail": "CSRF check failed"})

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.environment == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                key=CSRF_COOKIE,
                value=secrets.token_urlsafe(24),
                httponly=False,  # readable by the app so it can echo it in X-CSRF-Token
                secure=settings.cookie_secure,
                samesite="lax",
                path="/",
            )
        return response

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: the process is up. Does not touch the database."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"], response_model=None)
    def ready() -> dict[str, str] | JSONResponse:
        """Readiness: the database is reachable."""
        try:
            with db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("readiness check failed")
            return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
        return {"status": "ready", "database": "up"}

    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(workspace.router, prefix=api_prefix)
    app.include_router(discussions.router, prefix=api_prefix)
    app.include_router(work.router, prefix=api_prefix)
    app.include_router(activity.router, prefix=api_prefix)
    app.include_router(realtime.router, prefix=api_prefix)
    app.include_router(devices.router, prefix=api_prefix)
    app.include_router(bridge.router, prefix=api_prefix)
    app.include_router(agents.router, prefix=api_prefix)
    app.include_router(sessions.router, prefix=api_prefix)

    if settings.frontend_dist:
        _mount_frontend(app, Path(settings.frontend_dist))

    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built single-page app from the same origin as the API.

    Same origin keeps cookies, CSRF and WebSockets simple. Unknown API paths get a real 404 rather
    than the app shell, so clients never mistake a missing endpoint for a page.
    """
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        logger.warning("frontend_dist has no index.html; the UI will not be served", extra={"path": str(root)})
        return

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
