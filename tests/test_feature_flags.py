import pytest

from src.common.feature_flags import (
    FeatureFlagSpec,
    FeatureFlagValidationError,
    default_feature_flag_manifest,
    load_feature_flag_manifest,
    load_rendered_feature_flags,
    parse_feature_flag_manifest,
    validate_feature_flag_manifest,
    validate_rendered_feature_flags,
)


def test_missing_required_flag_blocks_rollout_without_printing_values():
    manifest = [
        FeatureFlagSpec(
            name="strict_scheduler_worker_contract",
            default=True,
            owner="orchestrator-platform",
            description="Contract rollout guard.",
        )
    ]
    rendered = {
        "scheduler": {
            "strict_scheduler_worker_contract": True,
        },
        "worker": {},
    }

    with pytest.raises(FeatureFlagValidationError) as exc:
        validate_rendered_feature_flags(rendered, manifest)

    message = str(exc.value)
    assert "worker" in message
    assert "strict_scheduler_worker_contract" in message
    assert "orchestrator-platform" in message
    assert "required flag is missing" in message
    assert "True" not in message


def test_scheduler_worker_mismatch_is_sanitized():
    manifest = [
        FeatureFlagSpec(
            name="private_rollout_token_seed",
            default="expected-secret-default",
            owner="security-platform",
            description="Sensitive rollout seed.",
            enforce_default=False,
        )
    ]
    rendered = {
        "scheduler": {
            "feature_flags": {
                "private_rollout_token_seed": "scheduler-secret-value",
            }
        },
        "worker": {
            "feature_flags": {
                "private_rollout_token_seed": "worker-secret-value",
            }
        },
    }

    with pytest.raises(FeatureFlagValidationError) as exc:
        validate_rendered_feature_flags(rendered, manifest)

    message = str(exc.value)
    assert "private_rollout_token_seed" in message
    assert "differs from scheduler" in message
    assert "scheduler-secret-value" not in message
    assert "worker-secret-value" not in message
    assert "expected-secret-default" not in message


def test_non_default_rendered_value_blocks_rollout():
    manifest = [
        FeatureFlagSpec(
            name="durable_completion_events",
            default=True,
            owner="runtime-platform",
            description="Completion events require durable state.",
        )
    ]
    rendered = {
        "scheduler": {"durable_completion_events": True},
        "worker": {"durable_completion_events": False},
    }

    with pytest.raises(FeatureFlagValidationError) as exc:
        validate_rendered_feature_flags(rendered, manifest)

    message = str(exc.value)
    assert "documented default" in message
    assert "False" not in message
    assert "True" not in message


def test_matching_scheduler_and_worker_defaults_pass():
    manifest = [
        FeatureFlagSpec(
            name="strict_scheduler_worker_contract",
            default=True,
            owner="orchestrator-platform",
            description="Contract rollout guard.",
        )
    ]
    rendered = {
        "scheduler": {
            "feature_flags": {
                "strict_scheduler_worker_contract": True,
            }
        },
        "worker": {
            "feature_flags": {
                "strict_scheduler_worker_contract": True,
            }
        },
    }

    validate_rendered_feature_flags(rendered, manifest)


def test_manifest_requires_default_owner_description_and_unique_names():
    with pytest.raises(FeatureFlagValidationError) as exc:
        validate_feature_flag_manifest(
            [
                {
                    "name": "strict_scheduler_worker_contract",
                    "owner": "orchestrator-platform",
                },
                {
                    "name": "strict_scheduler_worker_contract",
                    "default": True,
                    "owner": "orchestrator-platform",
                    "description": "Duplicate name.",
                },
            ]
        )

    message = str(exc.value)
    assert "documented default" in message
    assert "description" in message
    assert "duplicated" in message


def test_parse_manifest_defaults_target_services_to_scheduler_and_worker():
    specs = parse_feature_flag_manifest(
        {
            "feature_flags": [
                {
                    "name": "strict_scheduler_worker_contract",
                    "default": True,
                    "owner": "orchestrator-platform",
                    "description": "Contract rollout guard.",
                }
            ]
        }
    )

    assert specs[0].services == ("scheduler", "worker")


def test_loaders_accept_checked_in_manifest_and_rendered_services(tmp_path):
    rendered_file = tmp_path / "rendered.yaml"
    rendered_file.write_text(
        """
services:
  scheduler:
    feature_flags:
      strict_scheduler_worker_contract: true
      durable_completion_events: true
      safe_callback_registration: true
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

    manifest = load_feature_flag_manifest("config/required_feature_flags.json")
    rendered = load_rendered_feature_flags(rendered_file)

    validate_rendered_feature_flags(rendered, manifest)


def test_embedded_default_manifest_matches_checked_in_manifest():
    checked_in = load_feature_flag_manifest(
        "config/required_feature_flags.json"
    )
    embedded = default_feature_flag_manifest()

    assert checked_in == embedded
