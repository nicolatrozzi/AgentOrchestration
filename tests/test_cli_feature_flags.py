import pytest

from src.cli.main import _validate_deploy_manifest, cli
from src.common.feature_flags import FeatureFlagValidationError


def test_deploy_manifest_validates_inline_feature_flags(tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        """
feature_flags:
  - name: strict_scheduler_worker_contract
    default: true
    owner: orchestrator-platform
    description: Contract rollout guard.
services:
  scheduler:
    feature_flags:
      strict_scheduler_worker_contract: true
  worker:
    feature_flags:
      strict_scheduler_worker_contract: true
""",
        encoding="utf-8",
    )

    _validate_deploy_manifest(str(manifest))


def test_deploy_manifest_rejects_missing_worker_flag(tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        """
feature_flags:
  - name: strict_scheduler_worker_contract
    default: true
    owner: orchestrator-platform
    description: Contract rollout guard.
services:
  scheduler:
    feature_flags:
      strict_scheduler_worker_contract: true
  worker:
    feature_flags: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(FeatureFlagValidationError) as exc:
        _validate_deploy_manifest(str(manifest))

    message = str(exc.value)
    assert "worker" in message
    assert "strict_scheduler_worker_contract" in message
    assert "True" not in message


def test_validate_flags_cli_uses_embedded_default_manifest(
    tmp_path,
    monkeypatch,
    capsys,
):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(
        """
services:
  scheduler:
    feature_flags:
      strict_scheduler_worker_contract: true
      durable_completion_events: true
  worker:
    feature_flags:
      strict_scheduler_worker_contract: true
      durable_completion_events: true
  api:
    feature_flags:
      safe_callback_registration: true
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["ao", "validate-flags", str(rendered)],
    )

    cli()

    captured = capsys.readouterr()
    assert "Feature flag validation passed" in captured.out
