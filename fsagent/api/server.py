"""FastAPI server for the fsagent frontend integration."""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from fsagent.api.persistence import InMemorySessionStore, JsonlSessionStore, SessionStore
from fsagent.api.schemas import (
    HealthResponse,
    ModelConfigItem,
    ModelConfigResponse,
    ReviewDecisionRequest,
    ReviewRequest,
    RunRequest,
    SessionResponse,
)
from fsagent.api.service import (
    CheckpointMissingError,
    FsAgentApiService,
    ReviewConflictError,
    ReviewNotFoundError,
    SessionNotFoundError,
)
from fsagent.observability import configure_logging, get_logger
from fsagent.runtime.model_config import FsAgentEnv, load_model_catalog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from starlette.responses import Response

logger = get_logger(__name__)
SESSION_STORE_PATH_ENV = "FSAGENT_SESSION_STORE_PATH"


def create_app(service: FsAgentApiService | None = None) -> FastAPI:  # noqa: C901, PLR0915
    """Create the HTTP API app."""
    configure_logging()
    resolved_service = service or FsAgentApiService(session_store=_session_store_from_env())
    app = FastAPI(title="fsagent API")
    app.middleware("http")(_log_http_request)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/api/model-config", response_model=ModelConfigResponse, response_model_by_alias=True)
    async def model_config() -> ModelConfigResponse:
        models = [ModelConfigItem(**item) for item in load_model_catalog(FsAgentEnv.from_sources())]
        return ModelConfigResponse(models=models)

    @app.post("/api/runs", response_model=SessionResponse, response_model_by_alias=True)
    async def create_run(request: RunRequest) -> SessionResponse:
        return await resolved_service.create_run(request)

    @app.post("/api/runs/stream")
    async def create_run_stream(request: RunRequest) -> StreamingResponse:
        return StreamingResponse(
            _session_sse(resolved_service.create_run_stream(request)),
            media_type="text/event-stream",
        )

    @app.get("/api/runs/{session_id}", response_model=SessionResponse, response_model_by_alias=True)
    async def get_run(session_id: str) -> SessionResponse:
        try:
            return await resolved_service.get_run(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc

    @app.post("/api/runs/{session_id}/review", response_model=SessionResponse, response_model_by_alias=True)
    async def review_plan(session_id: str, request: ReviewRequest) -> SessionResponse:
        try:
            return await resolved_service.review_plan(session_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        except CheckpointMissingError as exc:
            raise HTTPException(status_code=409, detail="Runtime checkpoint missing for session.") from exc
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Review not found.") from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{session_id}/review/stream")
    async def review_plan_stream(session_id: str, request: ReviewRequest) -> StreamingResponse:
        try:
            await resolved_service.get_run(session_id)
            stream = resolved_service.review_plan_stream(session_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        except CheckpointMissingError as exc:
            raise HTTPException(status_code=409, detail="Runtime checkpoint missing for session.") from exc
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Review not found.") from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(_session_sse(stream), media_type="text/event-stream")

    @app.post(
        "/api/runs/{session_id}/reviews/{review_id}/decision",
        response_model=SessionResponse,
        response_model_by_alias=True,
    )
    async def decide_review(session_id: str, review_id: str, request: ReviewDecisionRequest) -> SessionResponse:
        try:
            return await resolved_service.decide_review(session_id, review_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        except CheckpointMissingError as exc:
            raise HTTPException(status_code=409, detail="Runtime checkpoint missing for session.") from exc
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Review not found.") from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{session_id}/reviews/{review_id}/decision/stream")
    async def decide_review_stream(
        session_id: str,
        review_id: str,
        request: ReviewDecisionRequest,
    ) -> StreamingResponse:
        try:
            await resolved_service.get_run(session_id)
            stream = resolved_service.decide_review_stream(session_id, review_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        except CheckpointMissingError as exc:
            raise HTTPException(status_code=409, detail="Runtime checkpoint missing for session.") from exc
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Review not found.") from exc
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(_session_sse(stream), media_type="text/event-stream")

    return app


def _session_store_from_env(env: dict[str, str] | None = None) -> SessionStore:
    """Create the configured session store for server startup."""
    values = os.environ if env is None else env
    path = values.get(SESSION_STORE_PATH_ENV, "").strip()
    if path:
        return JsonlSessionStore(path)
    return InMemorySessionStore()


async def _log_http_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        logger.exception(
            "HTTP request failed",
            extra={
                "event": "http.request.failed",
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise
    duration_ms = round((perf_counter() - started) * 1000, 2)
    logger.info(
        "HTTP request completed",
        extra={
            "event": "http.request.completed",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


app = create_app()


def main() -> None:
    """Run the development API server."""
    configure_logging()
    uvicorn.run("fsagent.api.server:app", host="127.0.0.1", port=8000, reload=True, access_log=False)


async def _session_sse(sessions: AsyncIterator[SessionResponse]) -> AsyncIterator[str]:
    async for session in sessions:
        payload = session.model_dump(by_alias=True)
        yield f"event: session\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
