"""Feature flag manifest and production rollout validation."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import yaml


_MISSING = object()


class FeatureFlagValidationError(ValueError):
    """Raised when a feature flag manifest or rollout artifact is invalid."""

    def __init__(self, violations: Sequence[str]):
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


@dataclass(frozen=True)
class FeatureFlagSpec:
    """Required feature flag metadata for deployment validation."""

    name: str
    default: Any
    owner: str
    description: str
    services: Tuple[str, ...] = ("scheduler", "worker")
    required: bool = True
    enforce_default: bool = True


def default_feature_flag_manifest() -> Tuple[FeatureFlagSpec, ...]:
    """Return the built-in required flag manifest for production rollout."""

    return validate_feature_flag_manifest(
        [
            FeatureFlagSpec(
                name="strict_scheduler_worker_contract",
                default=True,
                owner="orchestrator-platform",
                description=(
                    "Keeps scheduler and worker task-contract checks enabled "
                    "during production rollout."
                ),
            ),
            FeatureFlagSpec(
                name="durable_completion_events",
                default=True,
                owner="runtime-platform",
                description=(
                    "Requires durable task state to be recorded before "
                    "completion side effects are emitted."
                ),
            ),
            FeatureFlagSpec(
                name="safe_callback_registration",
                default=True,
                owner="api-platform",
                description=(
                    "Requires integration callback URLs to pass shared "
                    "service validation before registration."
                ),
                services=("api",),
            ),
        ]
    )


def load_feature_flag_manifest(
    path: Union[str, Path],
) -> Tuple[FeatureFlagSpec, ...]:
    """Load and validate a required feature flag manifest file."""

    raw = _load_structured_file(path)
    return parse_feature_flag_manifest(raw)


def parse_feature_flag_manifest(raw: Any) -> Tuple[FeatureFlagSpec, ...]:
    """Parse required flag metadata from a list or deploy-manifest mapping."""

    if isinstance(raw, Mapping):
        raw_flags = raw.get(
            "feature_flags",
            raw.get("flags", raw.get("required_flags")),
        )
    else:
        raw_flags = raw
    if raw_flags is None:
        raw_flags = []
    if not isinstance(raw_flags, list):
        raise FeatureFlagValidationError(["feature_flags must be a list"])

    return validate_feature_flag_manifest(raw_flags)


def load_rendered_feature_flags(
    path: Union[str, Path],
) -> Dict[str, Dict[str, Any]]:
    """Load rendered per-service flag values from JSON or YAML."""

    raw = _load_structured_file(path)
    return normalize_rendered_feature_flags(raw)


def normalize_rendered_feature_flags(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize supported rendered-service shapes to service -> flags."""

    if not isinstance(raw, Mapping):
        raise FeatureFlagValidationError(
            ["rendered feature flags must be an object"]
        )

    services = raw.get("services", raw)
    if not isinstance(services, Mapping):
        raise FeatureFlagValidationError(
            ["rendered feature flags must contain service mappings"]
        )

    normalized: Dict[str, Dict[str, Any]] = {}
    violations = []
    for service, raw_values in services.items():
        if not isinstance(service, str) or not service:
            violations.append("rendered feature flag service name is invalid")
            continue

        values = _extract_service_flags(raw_values)
        if values is None:
            violations.append(
                f"rendered feature flags for service '{service}' "
                "must be a mapping"
            )
            continue
        normalized[service] = dict(values)

    if violations:
        raise FeatureFlagValidationError(violations)
    return normalized


def validate_feature_flag_manifest(
    manifest: Iterable[Union[Mapping[str, Any], FeatureFlagSpec]],
) -> Tuple[FeatureFlagSpec, ...]:
    """Validate required flag metadata before use as a deploy gate."""

    specs = []
    seen = set()
    violations = []

    for index, entry in enumerate(manifest):
        if not isinstance(entry, (FeatureFlagSpec, Mapping)):
            violations.append(f"feature flag entry {index} must be an object")
            continue

        name = (
            entry.name
            if isinstance(entry, FeatureFlagSpec)
            else _clean_string(entry.get("name"))
        )
        if name:
            if name in seen:
                violations.append(f"feature flag '{name}' is duplicated")
            seen.add(name)

        try:
            spec = (
                entry
                if isinstance(entry, FeatureFlagSpec)
                else _coerce_spec(entry)
            )
        except FeatureFlagValidationError as exc:
            violations.extend(exc.violations)
            continue

        label = spec.name or "<unnamed>"
        if not spec.name:
            violations.append("feature flag entry is missing name")
        if spec.default is _MISSING:
            violations.append(
                f"feature flag '{label}' is missing documented default"
            )
        if not spec.owner:
            violations.append(f"feature flag '{label}' is missing owner")
        if not spec.description:
            violations.append(f"feature flag '{label}' is missing description")
        if not spec.services:
            violations.append(
                f"feature flag '{label}' must list target services"
            )
        elif len(set(spec.services)) != len(spec.services):
            violations.append(
                f"feature flag '{label}' lists duplicate target services"
            )

        specs.append(spec)

    if violations:
        raise FeatureFlagValidationError(violations)
    return tuple(specs)


def validate_rendered_feature_flags(
    rendered_by_service: Mapping[str, Mapping[str, Any]],
    manifest: Iterable[Union[Mapping[str, Any], FeatureFlagSpec]],
) -> None:
    """Fail closed when rendered service flags are unsafe for rollout.

    Validation messages intentionally do not serialize flag values. This keeps
    scheduler/worker comparisons useful without leaking sensitive values into
    release logs, CI output, or integration callbacks.
    """

    specs = validate_feature_flag_manifest(manifest)
    rendered = normalize_rendered_feature_flags(rendered_by_service)
    violations = []

    for spec in specs:
        if not spec.required:
            continue

        observed: List[Tuple[str, Any]] = []
        for service in spec.services:
            values = rendered.get(service)
            if values is None:
                violations.append(
                    _issue(
                        service,
                        spec,
                        "service is missing rendered feature flags",
                    )
                )
                continue
            if spec.name not in values:
                violations.append(
                    _issue(service, spec, "required flag is missing")
                )
                continue

            value = values[spec.name]
            observed.append((service, value))
            if spec.enforce_default and value != spec.default:
                violations.append(
                    _issue(
                        service,
                        spec,
                        "rendered value does not match documented default",
                    )
                )

        violations.extend(_compare_service_values(spec, observed))

    if violations:
        raise FeatureFlagValidationError(violations)


def validate_deploy_manifest(path: Union[str, Path]) -> None:
    """Validate inline feature flag metadata inside a deployment manifest."""

    raw = _load_structured_file(path)
    if not isinstance(raw, Mapping) or "feature_flags" not in raw:
        return

    manifest = parse_feature_flag_manifest(raw)
    rendered = normalize_rendered_feature_flags(raw)
    validate_rendered_feature_flags(rendered, manifest)


def _coerce_spec(entry: Mapping[str, Any]) -> FeatureFlagSpec:
    violations = []

    name = _clean_string(entry.get("name"))
    label = name or "<unnamed>"
    if not name:
        violations.append("feature flag entry is missing name")

    default = entry.get("default", _MISSING)
    if default is _MISSING:
        violations.append(
            f"feature flag '{label}' is missing documented default"
        )

    owner = _clean_string(entry.get("owner"))
    if not owner:
        violations.append(f"feature flag '{label}' is missing owner")

    description = _clean_string(entry.get("description"))
    if not description:
        violations.append(f"feature flag '{label}' is missing description")

    raw_services = entry.get("services", ("scheduler", "worker"))
    services = _coerce_services(raw_services, label, violations)

    if violations:
        raise FeatureFlagValidationError(violations)

    return FeatureFlagSpec(
        name=name,
        default=default,
        owner=owner,
        description=description,
        services=services,
        required=bool(entry.get("required", True)),
        enforce_default=bool(entry.get("enforce_default", True)),
    )


def _coerce_services(
    raw_services: Any,
    label: str,
    violations: List[str],
) -> Tuple[str, ...]:
    if not isinstance(raw_services, (list, tuple)):
        violations.append(f"feature flag '{label}' must list target services")
        return ()

    services = []
    for raw_service in raw_services:
        service = _clean_string(raw_service)
        if not service:
            violations.append(
                f"feature flag '{label}' has an invalid target service"
            )
            continue
        services.append(service)
    if not services:
        violations.append(f"feature flag '{label}' must list target services")
    return tuple(services)


def _extract_service_flags(raw_values: Any) -> Optional[Mapping[str, Any]]:
    if not isinstance(raw_values, Mapping):
        return None

    for key in ("feature_flags", "flags"):
        nested = raw_values.get(key)
        if nested is not None:
            return nested if isinstance(nested, Mapping) else None
    return raw_values


def _compare_service_values(
    spec: FeatureFlagSpec,
    observed: Sequence[Tuple[str, Any]],
) -> List[str]:
    if len(observed) < 2:
        return []

    reference_service, reference_value = observed[0]
    violations = []
    for service, value in observed[1:]:
        if value != reference_value:
            violations.append(
                _issue(
                    service,
                    spec,
                    f"rendered value differs from {reference_service}",
                )
            )
    return violations


def _issue(service: str, spec: FeatureFlagSpec, reason: str) -> str:
    return f"service={service} flag={spec.name} owner={spec.owner}: {reason}"


def _load_structured_file(path: Union[str, Path]) -> Any:
    with open(path, encoding="utf-8") as file:
        text = file.read()

    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text) or {}


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
