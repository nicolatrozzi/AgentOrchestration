from pathlib import Path

import yaml

from scripts.audit_docker_metadata import audit_metadata, resolve_runtime


def test_metadata_audit_passes_for_safe_history_and_runtime_labels():
    result = audit_metadata(
        history_lines=[
            "/bin/sh -c python -m pip install --no-cache-dir /tmp/app.whl",
            "CMD [\"uvicorn\", \"src.api.server:create_app\"]",
        ],
        labels={
            "org.opencontainers.image.title": "Agent Orchestrator",
            "org.opencontainers.image.description": "API image",
            "org.opencontainers.image.version": "2.4.1",
        },
        forbidden_values=[
            "sentinel-build-config",
            "AO_BUILD_CONFIG",
        ],
    )

    assert result.passed is True


def test_metadata_audit_rejects_forbidden_history_without_echoing_secret():
    secret = "sentinel-build-config"

    result = audit_metadata(
        history_lines=[
            f"/bin/sh -c echo {secret}",
        ],
        labels={
            "org.opencontainers.image.title": "Agent Orchestrator",
        },
        forbidden_values=[secret],
    )

    assert result.passed is False
    assert result.findings[0].location == "history[1]"
    assert secret not in result.findings[0].reason


def test_metadata_audit_rejects_unapproved_runtime_labels():
    result = audit_metadata(
        history_lines=[],
        labels={
            "org.opencontainers.image.title": "Agent Orchestrator",
            "com.example.build-token": "redacted",
        },
        forbidden_values=[],
    )

    assert result.passed is False
    assert result.findings[0].location == "label:com.example.build-token"


def test_dockerfile_keeps_build_args_out_of_runtime_stage():
    dockerfile = Path("Dockerfile").read_text()
    runtime_stage = dockerfile.split("FROM python:3.11-slim AS runtime", 1)[1]

    assert "ARG AO_BUILD_CONFIG" not in runtime_stage
    assert "ARG AO_BUILD_TOKEN" not in runtime_stage
    assert "ARG AO_PACKAGE_INDEX_URL" not in runtime_stage
    assert "org.opencontainers.image.title" in runtime_stage
    assert "com.example" not in runtime_stage


def test_ci_builds_image_and_runs_metadata_audit():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    docker_job = workflow["jobs"]["docker-metadata"]
    joined_steps = "\n".join(
        step.get("run", "")
        for step in docker_job["steps"]
    )

    assert "docker build" in joined_steps
    assert "AO_BUILD_SENTINEL" in joined_steps
    assert "scripts/audit_docker_metadata.py" in joined_steps
    assert "--forbidden AO_BUILD_CONFIG" in joined_steps


def test_audit_runtime_auto_prefers_docker_then_podman(monkeypatch):
    available = {"podman": "/usr/bin/podman"}

    monkeypatch.setattr(
        "scripts.audit_docker_metadata.shutil.which",
        lambda command: available.get(command),
    )

    assert resolve_runtime("auto") == "podman"
