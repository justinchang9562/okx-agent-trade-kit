from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Cookie, Header, HTTPException, Request, WebSocket

SESSION_COOKIE = "okx_dashboard_session"


@dataclass(frozen=True)
class Session:
    cookie: str
    csrf: str


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, float]] = {}
        self._websockets: dict[str, dict[str, float]] = {}
        self._lock = threading.RLock()

    def create(self) -> Session:
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        with self._lock:
            cutoff = time.monotonic() - 8 * 60 * 60
            expired = [cookie for cookie, (_csrf, created) in self._sessions.items() if created < cutoff]
            for cookie in expired:
                self._sessions.pop(cookie, None)
                self._websockets.pop(cookie, None)
            if len(self._sessions) >= 64:
                oldest = min(self._sessions, key=lambda cookie: self._sessions[cookie][1])
                self._sessions.pop(oldest, None)
                self._websockets.pop(oldest, None)
            self._sessions[session.cookie] = (session.csrf, time.monotonic())
        return session

    def valid(self, cookie: str | None, csrf: str | None = None) -> bool:
        if not cookie:
            return False
        with self._lock:
            record = self._sessions.get(cookie)
        if record is None:
            return False
        expected, created_at = record
        if time.monotonic() - created_at > 8 * 60 * 60:
            with self._lock:
                self._sessions.pop(cookie, None)
                self._websockets.pop(cookie, None)
            return False
        return csrf is None or bool(csrf) and hmac.compare_digest(expected, csrf)

    def register_websocket(self, cookie: str) -> str:
        connection_id = secrets.token_urlsafe(16)
        with self._lock:
            if cookie in self._sessions:
                self._websockets.setdefault(cookie, {})[connection_id] = time.monotonic()
        return connection_id

    def mark_websocket(self, cookie: str, connection_id: str) -> bool:
        if not self.valid(cookie):
            return False
        with self._lock:
            connections = self._websockets.get(cookie)
            if cookie not in self._sessions or connections is None or connection_id not in connections:
                return False
            connections[connection_id] = time.monotonic()
            return True

    def clear_websocket(self, cookie: str, connection_id: str) -> None:
        with self._lock:
            connections = self._websockets.get(cookie)
            if connections is None:
                return
            connections.pop(connection_id, None)
            if not connections:
                self._websockets.pop(cookie, None)

    def websocket_fresh(self, cookie: str | None, max_age_seconds: float = 8.0) -> bool:
        if not cookie:
            return False
        with self._lock:
            connections = tuple(self._websockets.get(cookie, {}).values())
        return bool(connections) and time.monotonic() - max(connections) <= max_age_seconds


def session_manager(request: Request) -> SessionManager:
    return request.app.state.sessions


def require_session(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> str:
    if not session_manager(request).valid(session_cookie):
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED"})
    return session_cookie or ""


def require_write_session(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> str:
    if not csrf or not session_manager(request).valid(session_cookie, csrf):
        raise HTTPException(status_code=403, detail={"code": "CSRF_OR_SESSION_INVALID"})
    return session_cookie or ""


def websocket_session(websocket: WebSocket) -> str | None:
    cookie = websocket.cookies.get(SESSION_COOKIE)
    manager: SessionManager = websocket.app.state.sessions
    origin = websocket.headers.get("origin", "")
    parsed = urlparse(origin)
    local_hosts = {"127.0.0.1", "localhost", "testserver"}
    if not manager.valid(cookie) or parsed.scheme not in {"http", "https"} or parsed.hostname not in local_hosts:
        return None
    return cookie
