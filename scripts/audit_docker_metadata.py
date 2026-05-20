"""Audit final Docker image metadata for leaked build-only values."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


DEFAULT_ALLOWED_LABELS = frozenset(
    {
        "org.opencontainers.image.title",
        "org.opencontainers.image.description",
        "org.opencontainers.image.version",
        "io.buildah.version",
    }
)


@dataclass(frozen=True)
class AuditFinding:
    location: str
    reason: str


@dataclass(frozen=True)
class AuditResult:
    findings: Sequence[AuditFinding]

    @property
    def passed(self) -> bool:
        return not self.findings


def audit_metadata(
    history_lines: Iterable[str],
    labels: Dict[str, str],
    forbidden_values: Iterable[str],
    allowed_labels: Iterable[str] = DEFAULT_ALLOWED_LABELS,
) -> AuditResult:
    findings: List[AuditFinding] = []
    forbidden = [value for value in forbidden_values if value]
    allowed = set(allowed_labels)

    for line_number, line in enumerate(history_lines, start=1):
        for forbidden_value in forbidden:
            if forbidden_value in line:
                findings.append(
                    AuditFinding(
                        location=f"history[{line_number}]",
                        reason="contains forbidden build-only metadata",
                    )
                )
                break

    for key, value in labels.items():
        if key not in allowed:
            findings.append(
                AuditFinding(
                    location=f"label:{key}",
                    reason="runtime label is not in the approved allow-list",
                )
            )
            continue
        for forbidden_value in forbidden:
            if forbidden_value in key or forbidden_value in value:
                findings.append(
                    AuditFinding(
                        location=f"label:{key}",
                        reason="contains forbidden build-only metadata",
                    )
                )
                break

    return AuditResult(tuple(findings))


def resolve_runtime(runtime: str) -> str:
    if runtime != "auto":
        if shutil.which(runtime) is None:
            raise RuntimeError(f"{runtime} executable not found")
        return runtime

    for candidate in ("docker", "podman"):
        if shutil.which(candidate) is not None:
            return candidate
    raise RuntimeError("neither docker nor podman is available")


def image_history(runtime: str, image: str) -> List[str]:
    completed = subprocess.run(
        [
            runtime,
            "history",
            "--no-trunc",
            "--format",
            "{{.CreatedBy}}",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()


def image_labels(runtime: str, image: str) -> Dict[str, str]:
    completed = subprocess.run(
        [runtime, "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    inspected = json.loads(completed.stdout)
    if not inspected:
        return {}
    labels = inspected[0].get("Config", {}).get("Labels") or {}
    return {str(key): str(value) for key, value in labels.items()}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit final Docker history and labels.",
    )
    parser.add_argument("--image", required=True, help="Docker image tag")
    parser.add_argument(
        "--runtime",
        choices=("auto", "docker", "podman"),
        default="auto",
        help="Container runtime used for image inspection",
    )
    parser.add_argument(
        "--forbidden",
        action="append",
        default=[],
        help="Forbidden build-only value or key to search for",
    )
    parser.add_argument(
        "--allowed-label",
        action="append",
        default=None,
        help="Approved runtime label key; may be repeated",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    runtime = resolve_runtime(args.runtime)
    result = audit_metadata(
        image_history(runtime, args.image),
        image_labels(runtime, args.image),
        args.forbidden,
        args.allowed_label or DEFAULT_ALLOWED_LABELS,
    )
    if result.passed:
        print("Docker metadata audit passed")
        return 0

    print("Docker metadata audit failed", file=sys.stderr)
    for finding in result.findings:
        print(
            f"- {finding.location}: {finding.reason}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
