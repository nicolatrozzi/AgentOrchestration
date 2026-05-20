import json
import sys

import pytest

from src.cli.main import cli


def test_deploy_cli_runs_migration_gate_before_success(
    monkeypatch,
    tmp_path,
    capsys,
):
    manifest = tmp_path / "agent.json"
    manifest.write_text(
        json.dumps(
            {
                "deployment": {
                    "current_version": "v1",
                    "target_version": "v2",
                    "reversible": True,
                    "migrations": [
                        {
                            "name": "add_task_state",
                            "backward_compatible": True,
                            "command": [
                                sys.executable,
                                "-c",
                                "print('migration complete')",
                            ],
                        }
                    ],
                }
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", str(manifest)])

    cli()

    output = capsys.readouterr()
    assert "Deployment ready: v2 serving after 1 migration(s)" in output.out
    assert output.err == ""


def test_deploy_cli_exits_nonzero_when_migration_fails(
    monkeypatch,
    tmp_path,
    capsys,
):
    manifest = tmp_path / "agent.json"
    manifest.write_text(
        json.dumps(
            {
                "deployment": {
                    "current_version": "v1",
                    "target_version": "v2",
                    "migrations": [
                        {
                            "name": "backfill_task_state",
                            "backward_compatible": True,
                            "command": [
                                sys.executable,
                                "-c",
                                "import sys; sys.exit(9)",
                            ],
                        }
                    ],
                }
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", str(manifest)])

    with pytest.raises(SystemExit) as exc:
        cli()

    output = capsys.readouterr()
    assert exc.value.code == 2
    assert output.out == ""
    assert "Rollout blocked" in output.err
    assert "backfill_task_state" in output.err
