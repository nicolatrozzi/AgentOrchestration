"""Migration gates for safe application rollouts."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
)

import yaml


MigrationHandler = Callable[[], bool]


class MigrationGateError(Exception):
    """Raised when a release cannot safely continue to application rollout."""


class MigrationCompatibility(str, Enum):
    UNKNOWN = "unknown"
    BACKWARD_COMPATIBLE = "backward_compatible"
    BREAKING = "breaking"


class DeploymentStatus(str, Enum):
    DEPLOYED = "deployed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MigrationJob:
    """Single migration that must pass before traffic can shift."""

    name: str
    command: Optional[Sequence[str]] = None
    backward_compatible: Optional[bool] = None
    required: bool = True
    timeout_seconds: Optional[float] = None
    handler: Optional[MigrationHandler] = None

    def compatibility(self) -> MigrationCompatibility:
        if self.backward_compatible is True:
            return MigrationCompatibility.BACKWARD_COMPATIBLE
        if self.backward_compatible is False:
            return MigrationCompatibility.BREAKING
        return MigrationCompatibility.UNKNOWN

    def run(self) -> "MigrationResult":
        try:
            if self.handler is not None:
                ok = self.handler()
                if ok is False:
                    return MigrationResult(
                        self.name,
                        False,
                        "handler returned false",
                    )
                return MigrationResult(self.name, True)

            if self.command is None:
                return MigrationResult(
                    self.name,
                    False,
                    "no migration command",
                )

            completed = subprocess.run(
                list(self.command),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode == 0:
                return MigrationResult(self.name, True)
            return MigrationResult(
                self.name,
                False,
                "command exited with status "
                f"{completed.returncode}: {completed.stderr.strip()}",
            )
        except subprocess.TimeoutExpired:
            return MigrationResult(self.name, False, "migration timed out")
        except Exception as exc:
            return MigrationResult(self.name, False, str(exc))


@dataclass(frozen=True)
class MigrationResult:
    name: str
    succeeded: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class CompatibilityReport:
    reversible: bool
    migrations: Dict[str, MigrationCompatibility]

    @property
    def compatible(self) -> bool:
        if not self.reversible:
            return True
        return all(
            state == MigrationCompatibility.BACKWARD_COMPATIBLE
            for state in self.migrations.values()
        )

    @property
    def incompatible_migrations(self) -> List[str]:
        return [
            name
            for name, state in self.migrations.items()
            if state != MigrationCompatibility.BACKWARD_COMPATIBLE
        ]


@dataclass(frozen=True)
class DeploymentPlan:
    name: str
    current_version: str
    target_version: str
    reversible: bool = False
    migrations: Sequence[MigrationJob] = field(default_factory=tuple)

    def compatibility_report(self) -> CompatibilityReport:
        return CompatibilityReport(
            reversible=self.reversible,
            migrations={
                migration.name: migration.compatibility()
                for migration in self.migrations
            },
        )


@dataclass
class TrafficGate:
    """Tracks whether the candidate version can receive traffic."""

    current_version: str
    active_version: str
    candidate_version: Optional[str] = None
    traffic_released: bool = False
    events: List[str] = field(default_factory=list)

    def hold_candidate(self, version: str) -> None:
        self.candidate_version = version
        self.traffic_released = False
        self.events.append(f"hold:{version}")

    def release_candidate(self) -> None:
        if self.candidate_version is None:
            raise MigrationGateError("no candidate version to release")
        self.active_version = self.candidate_version
        self.traffic_released = True
        self.events.append(f"release:{self.active_version}")


@dataclass(frozen=True)
class DeploymentResult:
    status: DeploymentStatus
    active_version: str
    target_version: str
    traffic_released: bool
    migration_results: Sequence[MigrationResult]
    compatibility_report: CompatibilityReport
    events: Sequence[str]
    error: Optional[str] = None

    @property
    def deployed(self) -> bool:
        return self.status == DeploymentStatus.DEPLOYED


class DeploymentController:
    """Runs migration gates before application rollout traffic changes."""

    def __init__(self, traffic_gate: Optional[TrafficGate] = None):
        self.traffic_gate = traffic_gate

    def deploy(self, plan: DeploymentPlan) -> DeploymentResult:
        gate = self.traffic_gate or TrafficGate(
            current_version=plan.current_version,
            active_version=plan.current_version,
        )
        gate.hold_candidate(plan.target_version)
        report = plan.compatibility_report()

        if not report.compatible:
            return DeploymentResult(
                status=DeploymentStatus.BLOCKED,
                active_version=gate.active_version,
                target_version=plan.target_version,
                traffic_released=False,
                migration_results=(),
                compatibility_report=report,
                events=tuple(gate.events),
                error=(
                    "reversible release contains non-backward-compatible "
                    "migrations: "
                    + ", ".join(report.incompatible_migrations)
                ),
            )

        results: List[MigrationResult] = []
        for migration in plan.migrations:
            if not migration.required:
                continue
            gate.events.append(f"migration:start:{migration.name}")
            result = migration.run()
            results.append(result)
            gate.events.append(f"migration:end:{migration.name}")
            if not result.succeeded:
                return DeploymentResult(
                    status=DeploymentStatus.BLOCKED,
                    active_version=gate.active_version,
                    target_version=plan.target_version,
                    traffic_released=False,
                    migration_results=tuple(results),
                    compatibility_report=report,
                    events=tuple(gate.events),
                    error=f"migration {migration.name} failed: {result.error}",
                )

        gate.release_candidate()
        return DeploymentResult(
            status=DeploymentStatus.DEPLOYED,
            active_version=gate.active_version,
            target_version=plan.target_version,
            traffic_released=True,
            migration_results=tuple(results),
            compatibility_report=report,
            events=tuple(gate.events),
        )


def deploy_from_manifest(
    path: str,
    handlers: Optional[Mapping[str, MigrationHandler]] = None,
) -> DeploymentResult:
    plan = load_deployment_plan(path, handlers=handlers)
    return DeploymentController().deploy(plan)


def load_deployment_plan(
    path: str,
    handlers: Optional[Mapping[str, MigrationHandler]] = None,
) -> DeploymentPlan:
    raw = _load_manifest(path)
    return parse_deployment_plan(raw, handlers=handlers)


def parse_deployment_plan(
    raw: Mapping[str, Any],
    handlers: Optional[Mapping[str, MigrationHandler]] = None,
) -> DeploymentPlan:
    deployment = _section(raw, "deployment")
    release = _section(raw, "release")
    app = _section(raw, "app")

    name = _first_string(raw, deployment, release, app, key="name")
    name = name or "application"
    target_version = (
        _first_string(raw, deployment, release, app, key="target_version")
        or _first_string(raw, deployment, release, app, key="version")
        or "candidate"
    )
    current_version = (
        _first_string(raw, deployment, release, app, key="current_version")
        or _first_string(raw, deployment, release, app, key="previous_version")
        or "current"
    )
    reversible = bool(
        _first_value(raw, deployment, release, "reversible", default=False)
    )
    migrations_raw = (
        _first_value(raw, deployment, release, "migrations", default=None)
        or raw.get("migrations", [])
    )

    return DeploymentPlan(
        name=name,
        current_version=current_version,
        target_version=target_version,
        reversible=reversible,
        migrations=tuple(_parse_migrations(migrations_raw, handlers or {})),
    )


def _load_manifest(path: str) -> Mapping[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise MigrationGateError(f"manifest not found: {path}")
    with manifest_path.open() as handle:
        if manifest_path.suffix.lower() == ".json":
            raw = json.load(handle)
        else:
            raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise MigrationGateError("deployment manifest must be a mapping")
    return raw


def _parse_migrations(
    raw: Any,
    handlers: Mapping[str, MigrationHandler],
) -> Iterable[MigrationJob]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise MigrationGateError("migrations must be a list")

    jobs: List[MigrationJob] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MigrationGateError("migration entries must be mappings")
        name = item.get("name") or item.get("id")
        if not isinstance(name, str) or not name.strip():
            raise MigrationGateError(f"migration {index} is missing a name")

        command = _parse_command(item.get("command"))
        compatibility = _first_value(
            item,
            "backward_compatible",
            "backwards_compatible",
            "compatible",
            default=None,
        )
        jobs.append(
            MigrationJob(
                name=name,
                command=command,
                backward_compatible=(
                    None if compatibility is None else bool(compatibility)
                ),
                required=bool(item.get("required", True)),
                timeout_seconds=item.get("timeout_seconds")
                or item.get("timeout"),
                handler=handlers.get(name),
            )
        )
    return jobs


def _parse_command(raw: Any) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        return tuple(shlex.split(raw))
    if isinstance(raw, list) and all(isinstance(part, str) for part in raw):
        return tuple(raw)
    raise MigrationGateError(
        "migration command must be a string or string list"
    )


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    section = raw.get(key, {})
    return section if isinstance(section, Mapping) else {}


def _first_string(*sections: Mapping[str, Any], key: str) -> Optional[str]:
    for section in sections:
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_value(*sections_or_keys: Any, default: Any = None) -> Any:
    sections = [
        item for item in sections_or_keys
        if isinstance(item, Mapping)
    ]
    keys = [
        item for item in sections_or_keys
        if isinstance(item, str)
    ]
    for section in sections:
        for key in keys:
            if key in section:
                return section[key]
    return default
