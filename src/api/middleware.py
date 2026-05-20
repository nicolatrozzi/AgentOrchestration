"""API middleware components."""

import hashlib
import logging
import time
from contextvars import ContextVar, Token
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

AUDIT_ACTOR_HEADER = "X-Audit-Actor"
AUDIT_STATUS_HEADER = "X-Audit-Status"
AUDIT_ACTOR_STATE_KEY = "audit_actor"
AUTHENTICATED_ACTOR_STATE_KEY = "authenticated_actor"
AUTH_TOKEN_PATH = "/api/v2/auth/token"
PROTECTED_API_PREFIX = "/api/v2"
_current_audit_actor: ContextVar[Optional[str]] = ContextVar(
    "current_audit_actor",
    default=None,
)


def get_current_audit_actor() -> Optional[str]:
    return _current_audit_actor.get()


def _audit_actor_from_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"bearer:{digest[:16]}"


def _extract_bearer_token(header: str) -> Optional[str]:
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None

    token = token.strip()
    return token or None


def _requires_auth(request: Request) -> bool:
    path = request.url.path
    return path.startswith(PROTECTED_API_PREFIX) and path != AUTH_TOKEN_PATH


def _authenticated_actor(request: Request) -> Optional[str]:
    return getattr(request.state, AUTHENTICATED_ACTOR_STATE_KEY, None)


def _request_audit_actor(request: Request) -> Optional[str]:
    return getattr(request.state, AUDIT_ACTOR_STATE_KEY, None)


def _set_current_audit_actor(request: Request, actor: str) -> Token:
    setattr(request.state, AUDIT_ACTOR_STATE_KEY, actor)
    return _current_audit_actor.set(actor)


def _set_authenticated_actor(request: Request, actor: str) -> None:
    setattr(request.state, AUTHENTICATED_ACTOR_STATE_KEY, actor)


def _clear_request_audit_actor(request: Request) -> None:
    if hasattr(request.state, AUDIT_ACTOR_STATE_KEY):
        delattr(request.state, AUDIT_ACTOR_STATE_KEY)


def _clear_authenticated_actor(request: Request) -> None:
    if hasattr(request.state, AUTHENTICATED_ACTOR_STATE_KEY):
        delattr(request.state, AUTHENTICATED_ACTOR_STATE_KEY)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        if _requires_auth(request):
            token = _extract_bearer_token(
                request.headers.get("Authorization", ""),
            )
            if token is None:
                return Response(
                    status_code=401,
                    content="Unauthorized",
                    headers={AUDIT_STATUS_HEADER: "rejected"},
                )

            _set_authenticated_actor(request, _audit_actor_from_token(token))
            try:
                return await call_next(request)
            finally:
                _clear_authenticated_actor(request)

        return await call_next(request)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        actor = None
        actor_token = None
        audit_status = "public"

        try:
            if _requires_auth(request):
                actor = _authenticated_actor(request)
                if actor is None:
                    audit_status = "rejected"
                    return Response(
                        status_code=401,
                        content="Unauthorized",
                        headers={AUDIT_STATUS_HEADER: audit_status},
                    )

                actor_token = _set_current_audit_actor(request, actor)
                audit_status = "attached"

            response = await call_next(request)
            response.headers[AUDIT_STATUS_HEADER] = audit_status
            if actor is not None:
                response.headers[AUDIT_ACTOR_HEADER] = actor
            return response
        except Exception:
            logger.exception(
                "audit middleware failed path=%s actor=%s status=%s",
                request.url.path,
                actor or "anonymous",
                audit_status,
            )
            raise
        finally:
            logger.info(
                "audit middleware completed path=%s actor=%s status=%s",
                request.url.path,
                actor or "anonymous",
                audit_status,
            )
            if actor_token is not None:
                _current_audit_actor.reset(actor_token)
            _clear_request_audit_actor(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [
            timestamp
            for timestamp in self._requests[client_ip]
            if now - timestamp < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        start = time.time()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.time() - start
            logger.exception(
                "%s %s 500 %.3fs",
                request.method,
                request.url.path,
                duration,
            )
            raise
        else:
            duration = time.time() - start
            logger.info(
                "%s %s %s %.3fs",
                request.method,
                request.url.path,
                response.status_code,
                duration,
            )
            return response
        finally:
            _clear_request_audit_actor(request)

# 2019-03-01T18:35:19 update

# 2019-04-03T13:22:05 update

# 2019-04-30T17:18:49 update

# 2019-08-20T09:29:03 update

# 2019-08-30T15:52:06 update

# 2019-11-23T16:58:42 update

# 2020-02-18T10:04:07 update

# 2020-04-21T17:35:30 update

# 2020-05-22T11:10:34 update

# 2020-07-02T12:31:26 update

# 2020-07-05T13:52:59 update

# 2020-08-21T20:36:45 update

# 2021-01-19T09:17:15 update

# 2021-01-29T11:34:24 update

# 2021-02-04T15:21:21 update

# 2021-04-19T19:23:15 update

# 2021-05-20T16:50:15 update

# 2021-06-22T19:23:44 update

# 2021-09-09T13:44:55 update

# 2021-09-16T09:30:20 update

# 2021-10-14T20:42:33 update

# 2021-12-28T16:39:14 update

# 2022-01-26T19:07:27 update

# 2022-01-28T08:03:41 update

# 2022-03-23T12:17:02 update

# 2022-04-06T12:12:27 update

# 2022-04-21T14:53:01 update

# 2022-06-30T08:37:32 update

# 2022-07-06T10:44:45 update

# 2022-11-02T11:12:47 update

# 2022-11-15T20:54:21 update

# 2022-11-23T14:13:34 update

# 2023-01-26T10:03:44 update

# 2023-02-09T17:08:10 update

# 2023-02-16T10:04:00 update

# 2023-03-14T11:52:03 update

# 2023-04-10T12:42:07 update

# 2023-04-26T10:43:39 update

# 2023-06-27T08:18:07 update

# 2023-08-30T15:30:40 update

# 2023-08-30T14:10:05 update

# 2023-10-09T18:32:46 update

# 2023-11-21T20:35:55 update

# 2024-03-07T19:17:39 update

# 2024-04-01T18:06:19 update

# 2024-07-18T15:37:34 update

# 2024-07-25T09:21:53 update

# 2024-08-12T14:24:22 update

# 2024-11-18T08:50:54 update

# 2025-04-08T12:43:05 update

# 2025-06-03T08:10:47 update

# 2025-06-12T08:37:52 update

# 2025-06-17T08:36:56 update

# 2025-07-02T18:09:42 update

# 2025-07-22T12:39:21 update

# 2025-10-13T12:13:46 update

# 2025-12-05T09:44:22 update

# 2025-12-22T18:34:47 update

# 2026-01-26T15:36:23 update

# 2026-02-13T12:36:40 update

# 2026-02-26T11:07:15 update

# 2026-03-19T11:00:17 update

# 2026-03-27T12:58:53 update

# 2026-05-12T17:19:36 update
