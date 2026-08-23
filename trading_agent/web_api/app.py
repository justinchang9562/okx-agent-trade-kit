from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from trading_agent.config import load_config
from trading_agent.service import ServiceError, TradingService
from trading_agent.web_api.schemas import (
    ApprovalRequest,
    BacktestRequest,
    ConfirmationRequest,
    EnvironmentRequest,
    ModeRequest,
    RuntimeSettingsRequest,
)
from trading_agent.web_api.security import (
    SESSION_COOKIE,
    SessionManager,
    require_session,
    require_write_session,
    websocket_session,
)

API_PREFIX = "/api/v1"
SAFE_CLIENTS = {"127.0.0.1", "::1", "testclient"}


def create_app(service: TradingService | None = None) -> FastAPI:
    owned_service = service is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "service"):
            app.state.service = TradingService(load_config())
        yield
        if owned_service:
            await asyncio.to_thread(app.state.service.close)

    app = FastAPI(
        title="OKX Local Trading Dashboard API",
        version="1.0.0",
        description=(
            "Local-only control plane for the hardened OKX Demo trading core. "
            "Live execution is locked and not implemented."
        ),
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    if service is not None:
        app.state.service = service
    app.state.sessions = SessionManager()
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        client = request.client.host if request.client else ""
        if client not in SAFE_CLIENTS:
            return JSONResponse(status_code=403, content={"error": {"code": "LOCAL_ACCESS_ONLY"}})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; img-src 'self' data:"
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "details": exc.errors()}},
        )

    def current_service(request: Request) -> TradingService:
        return request.app.state.service

    @app.get(f"{API_PREFIX}/session", tags=["session"])
    async def create_session(request: Request, response: Response) -> dict[str, Any]:
        session = request.app.state.sessions.create()
        response.set_cookie(
            SESSION_COOKIE, session.cookie, httponly=True, secure=False,
            samesite="strict", path="/", max_age=8 * 60 * 60,
        )
        return {"csrf_token": session.csrf, "expires_in_seconds": 8 * 60 * 60}

    read_auth = [Depends(require_session)]
    write_auth = [Depends(require_write_session)]

    def require_fresh_websocket(request: Request, _session: str = Depends(require_write_session)) -> str:
        cookie = request.cookies.get(SESSION_COOKIE)
        if not request.app.state.sessions.websocket_fresh(cookie):
            raise ServiceError("WEBSOCKET_NOT_FRESH", 423)
        return cookie or ""

    high_risk_auth = [Depends(require_fresh_websocket)]

    @app.get(f"{API_PREFIX}/status", dependencies=read_auth, tags=["system"])
    async def status(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).status)

    @app.get(f"{API_PREFIX}/health", dependencies=read_auth, tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).refresh_health)

    @app.get(f"{API_PREFIX}/account", dependencies=read_auth, tags=["trading-data"])
    async def account(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).account)

    @app.get(f"{API_PREFIX}/scanner", dependencies=read_auth, tags=["trading-data"])
    async def scanner(request: Request) -> dict[str, Any]:
        return current_service(request).scanner_state()

    @app.post(f"{API_PREFIX}/scanner/run", dependencies=write_auth, tags=["actions"])
    async def run_scan(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).scan)

    @app.post(f"{API_PREFIX}/analyze/{{symbol}}", dependencies=write_auth, tags=["actions"])
    async def analyze(symbol: str, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).analyze, symbol)

    @app.get(f"{API_PREFIX}/signals", dependencies=read_auth, tags=["trading-data"])
    async def signals(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
        return await asyncio.to_thread(current_service(request).signals, limit)

    @app.get(f"{API_PREFIX}/plans", dependencies=read_auth, tags=["trading-data"])
    async def plans(
        request: Request, status_filter: str | None = Query(None, alias="status"),
        limit: int = Query(200, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(current_service(request).plans, status_filter, limit)

    @app.get(f"{API_PREFIX}/plans/pending", dependencies=read_auth, tags=["trading-data"])
    async def pending_plans(request: Request) -> list[dict[str, Any]]:
        return await asyncio.to_thread(current_service(request).pending_plans)

    @app.get(f"{API_PREFIX}/orders", dependencies=read_auth, tags=["trading-data"])
    async def orders(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).orders)

    @app.get(f"{API_PREFIX}/positions", dependencies=read_auth, tags=["trading-data"])
    async def positions(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).positions)

    @app.get(f"{API_PREFIX}/trades", dependencies=read_auth, tags=["trading-data"])
    async def trades(request: Request) -> list[dict[str, Any]]:
        return await asyncio.to_thread(current_service(request).trades)

    @app.get(f"{API_PREFIX}/logs", dependencies=read_auth, tags=["observability"])
    async def logs(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[str]:
        return await asyncio.to_thread(current_service(request).logs, limit)

    @app.get(f"{API_PREFIX}/audit-log", dependencies=read_auth, tags=["observability"])
    async def audit_log(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
        return await asyncio.to_thread(current_service(request).audit_log, limit)

    @app.get(f"{API_PREFIX}/settings", dependencies=read_auth, tags=["settings"])
    async def settings(request: Request) -> dict[str, Any]:
        return current_service(request).settings()

    @app.put(f"{API_PREFIX}/settings/runtime", dependencies=write_auth, tags=["settings"])
    async def update_settings(body: RuntimeSettingsRequest, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(
            current_service(request).update_runtime_settings, body.scan_interval_seconds,
        )

    @app.post(f"{API_PREFIX}/environment", dependencies=write_auth, tags=["controls"])
    async def environment(body: EnvironmentRequest, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).set_environment, body.environment)

    @app.post(f"{API_PREFIX}/mode", dependencies=write_auth, tags=["controls"])
    async def mode(body: ModeRequest, request: Request) -> dict[str, Any]:
        if body.mode == "AUTO" and not request.app.state.sessions.websocket_fresh(
            request.cookies.get(SESSION_COOKIE)
        ):
            raise ServiceError("WEBSOCKET_NOT_FRESH", 423)
        return await asyncio.to_thread(current_service(request).set_mode, body.mode)

    @app.post(f"{API_PREFIX}/execution/arm", dependencies=high_risk_auth, tags=["controls"])
    async def arm(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).arm)

    @app.post(f"{API_PREFIX}/execution/disarm", dependencies=write_auth, tags=["controls"])
    async def disarm(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).disarm)

    @app.post(f"{API_PREFIX}/agent/start", dependencies=high_risk_auth, tags=["controls"])
    async def start_agent(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).start_agent)

    @app.post(f"{API_PREFIX}/agent/stop", dependencies=write_auth, tags=["controls"])
    async def stop_agent(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).stop_agent)

    @app.post(f"{API_PREFIX}/auto-demo/enable", dependencies=high_risk_auth, tags=["controls"])
    async def enable_auto(body: ConfirmationRequest, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).enable_auto_demo, body.confirmation)

    @app.post(f"{API_PREFIX}/auto-demo/disable", dependencies=write_auth, tags=["controls"])
    async def disable_auto(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).disable_auto_demo)

    @app.post(f"{API_PREFIX}/kill-switch", dependencies=write_auth, tags=["controls"])
    async def kill_switch(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).activate_kill_switch)

    @app.post(f"{API_PREFIX}/kill-switch/reset", dependencies=write_auth, tags=["controls"])
    async def reset_kill_switch(body: ConfirmationRequest, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).reset_kill_switch, body.confirmation)

    @app.post(f"{API_PREFIX}/plans/{{plan_id}}/preview", dependencies=high_risk_auth, tags=["approval"])
    async def approval_preview(plan_id: str, request: Request) -> dict[str, Any]:
        preview = await asyncio.to_thread(current_service(request).approval_preview, plan_id)
        if preview.get("status") != "READY_FOR_EXACT_APPROVAL":
            raise ServiceError(str(preview.get("reason") or "PLAN_NOT_EXECUTABLE"), 409)
        cookie = request.cookies.get(SESSION_COOKIE) or ""
        challenge = request.app.state.sessions.create_approval_challenge(
            cookie,
            plan_id,
            preview,
            float(current_service(request).config.rules["approval"].get("challenge_ttl_seconds", 15)),
        )
        return preview | challenge

    @app.post(f"{API_PREFIX}/plans/{{plan_id}}/approve", dependencies=high_risk_auth, tags=["approval"])
    async def approve(plan_id: str, body: ApprovalRequest, request: Request) -> dict[str, Any]:
        cookie = request.cookies.get(SESSION_COOKIE) or ""
        try:
            preview = request.app.state.sessions.consume_approval_challenge(
                cookie, plan_id, body.approval_challenge,
            )
        except ValueError as exc:
            raise ServiceError(str(exc), 409) from exc
        return await asyncio.to_thread(
            current_service(request).approve_plan_with_challenge,
            plan_id,
            preview,
        )

    @app.post(f"{API_PREFIX}/plans/{{plan_id}}/reject", dependencies=write_auth, tags=["approval"])
    async def reject(plan_id: str, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(current_service(request).reject_plan, plan_id)

    @app.post(f"{API_PREFIX}/backtest", dependencies=write_auth, tags=["backtest"])
    async def backtest(body: BacktestRequest, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(
            current_service(request).run_backtest, body.symbol, body.days, body.walk_forward,
        )

    @app.websocket(f"{API_PREFIX}/ws")
    async def websocket_stream(websocket: WebSocket) -> None:
        socket_session = websocket_session(websocket)
        if not socket_session:
            await websocket.close(code=4403, reason="SESSION_OR_ORIGIN_INVALID")
            return
        await websocket.accept()
        connection_id = websocket.app.state.sessions.register_websocket(socket_session)
        websocket.app.state.service.client_stream_connected(connection_id)
        service = websocket.app.state.service
        sequence = 0
        try:
            try:
                snapshot = await asyncio.to_thread(service.dashboard_snapshot)
                sequence = snapshot.get("sequence", 0)
                await websocket.send_json({
                    "schema_version": "1.0", "sequence": sequence,
                    "timestamp_ms": snapshot["timestamp_ms"], "type": "snapshot",
                    "reason": "INITIAL_CONNECT", "data": snapshot,
                })
            except Exception as exc:
                await websocket.send_json({
                    "schema_version": "1.0", "sequence": 0,
                    "timestamp_ms": 0, "type": "snapshot.error",
                    "reason": "DATA_UNAVAILABLE", "data": {"error_type": type(exc).__name__},
                })
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                except TimeoutError:
                    message = None
                if not websocket.app.state.sessions.websocket_registered(socket_session, connection_id):
                    await websocket.close(code=4401, reason="SESSION_EXPIRED")
                    return
                if message is not None:
                    valid_ack = (
                        isinstance(message, dict)
                        and set(message) == {"type", "sequence"}
                        and message.get("type") == "heartbeat.ack"
                        and isinstance(message.get("sequence"), int)
                        and not isinstance(message.get("sequence"), bool)
                        and 0 <= int(message["sequence"]) <= sequence
                    )
                    if not valid_ack or not websocket.app.state.sessions.acknowledge_websocket(
                        socket_session, connection_id, int(message.get("sequence", -1)),
                    ):
                        await websocket.close(code=4400, reason="INVALID_HEARTBEAT_ACK")
                        return
                    service.client_stream_acknowledged(connection_id)
                events = service.events_after(sequence)
                if events:
                    for event in events:
                        await websocket.send_json(event)
                    sequence = events[-1]["sequence"]
                else:
                    await websocket.send_json({
                        "schema_version": "1.0", "sequence": sequence,
                        "timestamp_ms": int(time.time() * 1000),
                        "type": "heartbeat", "reason": "PASS", "data": {},
                    })
        except Exception:
            return
        finally:
            websocket.app.state.sessions.clear_websocket(socket_session, connection_id)
            service.client_stream_disconnected(connection_id)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def dashboard_index() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

    else:
        @app.get("/", include_in_schema=False)
        async def dashboard_missing() -> JSONResponse:
            return JSONResponse({
                "status": "FRONTEND_NOT_BUILT",
                "build": "cd frontend && npm install && npm run build",
                "api_docs": f"{API_PREFIX}/docs",
            })

    return app
