import sys

import pytest

from src.deploy.migrations import (
    DeploymentController,
    DeploymentPlan,
    DeploymentStatus,
    MigrationGateError,
    MigrationJob,
    TrafficGate,
    load_deployment_plan,
)


def test_rollout_releases_traffic_only_after_migrations_pass():
    gate = TrafficGate(current_version="v1", active_version="v1")
    observed_active_versions = []

    def migration_handler():
        observed_active_versions.append(gate.active_version)
        return True

    plan = DeploymentPlan(
        name="workers",
        current_version="v1",
        target_version="v2",
        reversible=True,
        migrations=(
            MigrationJob(
                name="add_task_state_index",
                backward_compatible=True,
                handler=migration_handler,
            ),
        ),
    )

    result = DeploymentController(gate).deploy(plan)

    assert result.status == DeploymentStatus.DEPLOYED
    assert result.traffic_released is True
    assert result.active_version == "v2"
    assert observed_active_versions == ["v1"]
    assert result.events == (
        "hold:v2",
        "migration:start:add_task_state_index",
        "migration:end:add_task_state_index",
        "release:v2",
    )


def test_migration_failure_blocks_rollout_and_keeps_prior_version_serving():
    gate = TrafficGate(current_version="v1", active_version="v1")
    plan = DeploymentPlan(
        name="workers",
        current_version="v1",
        target_version="v2",
        migrations=(
            MigrationJob(
                name="backfill_task_state",
                backward_compatible=True,
                handler=lambda: False,
            ),
        ),
    )

    result = DeploymentController(gate).deploy(plan)

    assert result.status == DeploymentStatus.BLOCKED
    assert result.traffic_released is False
    assert result.active_version == "v1"
    assert gate.active_version == "v1"
    assert result.migration_results[0].succeeded is False
    assert "backfill_task_state" in result.error
    assert "release:v2" not in result.events


def test_reversible_release_blocks_unknown_or_breaking_migrations_before_run():
    called = []
    gate = TrafficGate(current_version="v1", active_version="v1")
    plan = DeploymentPlan(
        name="workers",
        current_version="v1",
        target_version="v2",
        reversible=True,
        migrations=(
            MigrationJob(
                name="drop_legacy_task_column",
                backward_compatible=False,
                handler=lambda: called.append("ran") or True,
            ),
        ),
    )

    result = DeploymentController(gate).deploy(plan)

    assert result.status == DeploymentStatus.BLOCKED
    assert result.migration_results == ()
    assert called == []
    assert result.compatibility_report.compatible is False
    assert result.compatibility_report.incompatible_migrations == [
        "drop_legacy_task_column"
    ]
    assert result.active_version == "v1"


def test_manifest_loader_accepts_nested_deployment_migrations(tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        """
deployment:
  name: worker-api
  current_version: v1
  target_version: v2
  reversible: true
  migrations:
    - name: add_task_attempts
      backward_compatible: true
      command:
        - python3
        - -c
        - "print('ok')"
"""
    )

    plan = load_deployment_plan(str(manifest))

    assert plan.name == "worker-api"
    assert plan.current_version == "v1"
    assert plan.target_version == "v2"
    assert plan.reversible is True
    assert plan.migrations[0].command == ("python3", "-c", "print('ok')")
    assert plan.compatibility_report().compatible is True


def test_manifest_loader_rejects_migration_without_name(tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        """
migrations:
  - backward_compatible: true
"""
    )

    with pytest.raises(MigrationGateError, match="missing a name"):
        load_deployment_plan(str(manifest))


def test_command_migration_result_blocks_on_nonzero_exit():
    job = MigrationJob(
        name="failing_command",
        command=(sys.executable, "-c", "import sys; sys.exit(3)"),
        backward_compatible=True,
    )

    result = job.run()

    assert result.succeeded is False
    assert "status 3" in result.error
