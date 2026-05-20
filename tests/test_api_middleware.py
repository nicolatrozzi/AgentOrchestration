import hashlib
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware import (
    AUDIT_ACTOR_HEADER,
    AUDIT_STATUS_HEADER,
    AuditMiddleware,
    AuthMiddleware,
    LoggingMiddleware,
    get_current_audit_actor,
)
from src.api.server import create_app


SECRET_TOKEN = "ticket-secret-123"
EXPECTED_DIGEST = hashlib.sha256(SECRET_TOKEN.encode()).hexdigest()[:16]
EXPECTED_ACTOR = f"bearer:{EXPECTED_DIGEST}"


def make_app():
    app = FastAPI()

    @app.get("/api/v2/protected")
    async def protected(request: Request):
        return {
            "state_actor": getattr(request.state, "audit_actor", None),
            "current_actor": get_current_audit_actor(),
        }

    @app.get("/api/v2/error")
    async def error():
        raise RuntimeError("request failed")

    @app.get("/public")
    async def public(request: Request):
        return {
            "state_actor": getattr(request.state, "audit_actor", None),
            "current_actor": get_current_audit_actor(),
        }

    app.add_middleware(AuditMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LoggingMiddleware)
    return app


def test_attaches_audit_actor_only_after_successful_auth(caplog):
    client = TestClient(make_app())
    caplog.set_level(logging.INFO, logger="src.api.middleware")

    response = client.get(
        "/api/v2/protected",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "state_actor": EXPECTED_ACTOR,
        "current_actor": EXPECTED_ACTOR,
    }
    assert response.headers[AUDIT_ACTOR_HEADER] == EXPECTED_ACTOR
    assert response.headers[AUDIT_STATUS_HEADER] == "attached"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert EXPECTED_ACTOR in messages
    assert SECRET_TOKEN not in messages


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer ",
        "Basic ticket-secret-123",
    ],
)
def test_rejected_requests_do_not_attach_audit_actor(authorization):
    client = TestClient(make_app())
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization

    response = client.get("/api/v2/protected", headers=headers)

    assert response.status_code == 401
    assert AUDIT_ACTOR_HEADER not in response.headers
    assert response.headers[AUDIT_STATUS_HEADER] == "rejected"

    follow_up = client.get("/public")
    assert follow_up.status_code == 200
    assert follow_up.json() == {"state_actor": None, "current_actor": None}
    assert AUDIT_ACTOR_HEADER not in follow_up.headers
    assert follow_up.headers[AUDIT_STATUS_HEADER] == "public"


def test_exception_path_clears_actor_and_keeps_secret_out_of_logs(caplog):
    client = TestClient(make_app(), raise_server_exceptions=False)
    caplog.set_level(logging.INFO, logger="src.api.middleware")

    response = client.get(
        "/api/v2/error",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert response.status_code == 500
    assert AUDIT_ACTOR_HEADER not in response.headers

    follow_up = client.get("/public")
    assert follow_up.status_code == 200
    assert follow_up.json() == {"state_actor": None, "current_actor": None}

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert EXPECTED_ACTOR in messages
    assert SECRET_TOKEN not in messages


def test_audit_middleware_fails_closed_without_authenticated_actor():
    app = FastAPI()
    handler_called = False

    @app.get("/api/v2/protected")
    async def protected():
        nonlocal handler_called
        handler_called = True
        return {"ok": True}

    app.add_middleware(AuditMiddleware)
    client = TestClient(app)

    response = client.get(
        "/api/v2/protected",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert response.status_code == 401
    assert response.headers[AUDIT_STATUS_HEADER] == "rejected"
    assert AUDIT_ACTOR_HEADER not in response.headers
    assert not handler_called


def test_create_app_orders_auth_before_audit_middleware():
    client = TestClient(create_app())

    rejected = client.get("/api/v2/agents")
    assert rejected.status_code == 401
    assert rejected.headers[AUDIT_STATUS_HEADER] == "rejected"
    assert AUDIT_ACTOR_HEADER not in rejected.headers

    accepted = client.get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert accepted.status_code == 200
    assert accepted.json()["agents"] == []
    assert accepted.headers[AUDIT_ACTOR_HEADER] == EXPECTED_ACTOR
    assert accepted.headers[AUDIT_STATUS_HEADER] == "attached"
