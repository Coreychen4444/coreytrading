"""Metadata-only offline inventory tests; every path points at a fake dataset."""
import json
from pathlib import Path

import pytest

from quant.ops import agent_doctor


def registry(tmp_path, data, subdir="claude"):
    path = tmp_path / subdir / "scheduled-tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def automation(tmp_path, text, name="test"):
    path = tmp_path / "codex" / name / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fake_resolver(provider):
    """Offline stand-in for agent_runners.resolve_binary: claude found, codex not."""
    found = provider == "claude"
    return {"provider": provider, "binary": f"/fake/bin/{provider}" if found else None,
            "source": "candidate" if found else "unresolved", "env_var": f"{provider.upper()}_BIN",
            "candidates": [f"/fake/candidates/{provider}"]}


def inspect(tmp_path, paths):
    return agent_doctor.inventory(claude_registry_paths=paths, codex_automation_dir=tmp_path / "codex",
                                  runner_resolver=fake_resolver)


def test_exports_only_allowed_metadata_and_never_opens_task_definition(tmp_path, monkeypatch):
    task = tmp_path / "SKILL.md"
    task.write_text("PRIVATE TASK BODY")
    path = registry(tmp_path, {"scheduledTasks": [{
        "id": "heartbeat", "name": "Portfolio heartbeat", "enabled": True,
        "model": "fake-model", "cronExpression": "0 1 * * *", "cwd": "/fake/project",
        "filePath": str(task), "lastRunAt": "2026-09-05T00:00:00Z",
        "prompt": "PRIVATE EMBEDDED PROMPT", "permissionMode": "do-not-export",
        "credentials": {"key": "SECRET"}, "description": "PRIVATE DESCRIPTION",
    }], "recordedSkips": {"private": "PRIVATE SKIP BODY"}})
    original = Path.read_text

    def only_metadata(self, *args, **kwargs):
        assert self != task
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", only_metadata)
    output = inspect(tmp_path, [path])
    item = output["claude"]["tasks"][0]
    assert item["id"] == "heartbeat" and item["enabled"] is True
    assert item["cron"] == "0 1 * * *" and item["filePath"] == str(task)
    assert item["registry_entry_present"] is True
    assert item["runtime_registered"] is None
    assert item["last_run_evidence"] == "timestamp_only"
    encoded = json.dumps(output)
    assert "credentials" not in item
    for private in ("PRIVATE", "SECRET", "permissionMode", "do-not-export"):
        assert private not in encoded


@pytest.mark.parametrize("shape", [
    [{"id": "daily", "cronExpression": "0 1 * * *", "enabled": False}],
    {"tasks": [{"id": "daily", "cronExpression": "0 1 * * *", "enabled": False}]},
    {"scheduled_tasks": {"daily": {"cronExpression": "0 1 * * *", "enabled": False}}},
    {"daily": {"cronExpression": "0 1 * * *", "enabled": False}},
    {"id": "daily", "cronExpression": "0 1 * * *", "enabled": False},
])
def test_common_registry_shapes_preserve_disabled_status(shape, tmp_path):
    output = inspect(tmp_path, [registry(tmp_path, shape)])
    assert len(output["claude"]["tasks"]) == 1
    assert output["claude"]["tasks"][0]["id"] == "daily"
    assert output["claude"]["tasks"][0]["enabled"] is False
    assert output["claude"]["tasks"][0]["last_run_evidence"] == "unavailable"


def test_codex_active_file_does_not_become_runtime_registration(tmp_path):
    automation(tmp_path, '''name = "Macro"
id = "ai"
status = "ACTIVE"
kind = "cron"
model = "fake-model"
cwds = ["/fake/project"]
projectId = "project-1"
rrule = "FREQ=DAILY;BYHOUR=5"
prompt = "PRIVATE PROMPT"
lastRunAt = 2026-09-05T00:00:00Z
[private]
token = "PRIVATE SECRET"
''')
    automation(tmp_path, 'name = "Paused"\nstatus = "PAUSED"\nprompt = "PRIVATE"', name="paused")
    output = inspect(tmp_path, [])
    tasks = {item["id"]: item for item in output["codex"]["tasks"]}
    active = tasks["ai"]
    assert active["configured_enabled"] is True
    assert active["runtime_registered"] is None
    assert active["cwd"] == ["/fake/project"]
    assert active["projectId"] == "project-1"
    assert active["lastRunAt"] == "2026-09-05T00:00:00+00:00"
    assert tasks["paused"]["configured_enabled"] is False
    assert "PRIVATE" not in json.dumps(output)
    assert "configuration only" in " ".join(output["limitations"])


def test_missing_invalid_and_unknown_shape_remain_explicit(tmp_path):
    missing = tmp_path / "missing" / "scheduled-tasks.json"
    invalid = registry(tmp_path, {}, "invalid")
    invalid.write_text('{"PRIVATE":')
    unknown = registry(tmp_path, {"not_a_registry": "PRIVATE"}, "unknown")
    ambiguous = registry(tmp_path, {"tasks": [], "scheduledTasks": []}, "ambiguous")
    output = inspect(tmp_path, [missing, invalid, unknown, ambiguous])
    assert not output["claude"]["tasks"]
    sources = output["claude"]["sources"]
    assert sources[0]["exists"] is False
    assert sources[1]["error"] == "metadata_read_or_parse_failed"
    assert sources[2]["error"] == sources[3]["error"] == "unknown_registry_shape"
    assert "PRIVATE" not in json.dumps(output)
    assert output["codex"]["directory_exists"] is False


def test_unsupported_auth_path_and_symlink_are_never_read(tmp_path, monkeypatch):
    credential = tmp_path / "auth.json"
    credential.write_text('{"token": "SECRET"}')
    linked = tmp_path / "scheduled-tasks.json"
    linked.symlink_to(credential)

    def do_not_read(*args, **kwargs):
        pytest.fail("forbidden file was read")

    monkeypatch.setattr(Path, "read_text", do_not_read)
    output = inspect(tmp_path, [credential, linked])
    assert all(item["error"] == "unsupported_metadata_path" for item in output["claude"]["sources"])


def test_multiple_registry_files_are_not_merged_into_a_live_claim(tmp_path):
    first = registry(tmp_path, {"tasks": [{"id": "daily", "enabled": True}]}, "old")
    second = registry(tmp_path, {"tasks": [{"id": "daily", "enabled": False}]}, "new")
    output = inspect(tmp_path, [first, second])
    assert len(output["claude"]["tasks"]) == 2
    assert output["claude"]["runtime_verified"] is False
    assert {item["enabled"] for item in output["claude"]["tasks"]} == {False, True}


def test_nested_unexpected_metadata_is_not_exported(tmp_path):
    path = registry(tmp_path, {"tasks": [{"id": "test", "enabled": "true",
                                          "model": {"token": "SECRET"}}]})
    automation(tmp_path, 'status = "UNKNOWN"\n[project]\nsecret = "SECRET"')
    output = inspect(tmp_path, [path])
    assert output["claude"]["tasks"][0]["enabled"] is None
    assert output["claude"]["tasks"][0]["model"] is None
    assert output["codex"]["tasks"][0]["project"] is None
    assert output["codex"]["tasks"][0]["configured_enabled"] is None
    assert "SECRET" not in json.dumps(output)


def test_cli_uses_injected_fake_inventory_and_prints_metadata(tmp_path, monkeypatch, capsys):
    fake = inspect(tmp_path, [])
    monkeypatch.setattr(agent_doctor, "inventory", lambda: fake)
    assert agent_doctor.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["read_only"] is True
    assert agent_doctor.main([]) == 0
    assert "unverified" in capsys.readouterr().out


def test_default_discovery_is_limited_to_exact_metadata_names(tmp_path, monkeypatch):
    claude_root = tmp_path / "claude"
    registry(tmp_path, {"tasks": [{"id": "ok"}]}, "claude/workspace/session")
    registry(tmp_path, {"tasks": [{"id": "too-deep"}]}, "claude/workspace/session/nested")
    ignored = tmp_path / "codex" / "auth.toml"
    ignored.parent.mkdir()
    ignored.write_text('token="PRIVATE"')
    monkeypatch.setattr(agent_doctor, "CLAUDE_SCHEDULE_REGISTRY_ROOT", claude_root)
    monkeypatch.setattr(agent_doctor, "CODEX_AUTOMATION_ROOT", tmp_path / "codex")
    monkeypatch.setattr(agent_doctor, "resolve_binary", fake_resolver)
    output = agent_doctor.inventory()
    assert [item["id"] for item in output["claude"]["tasks"]] == ["ok"]
    assert not output["codex"]["tasks"]
    assert output["runners"]["codex"]["resolved"] is False, "default resolver is the runner's path check"


def test_runner_resolution_is_path_only_and_reports_unresolved(tmp_path, monkeypatch):
    """: codex 不在定时任务 PATH 上 (住在 ChatGPT.app 里)。doctor 只按路径报解析
    结果, 不执行 CLI; 未解析 = 该 profile 主用 + 次选同时不可用, 对应角色 fail closed。"""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("doctor executed a CLI"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("doctor executed a CLI"))
    output = inspect(tmp_path, [])
    assert output["runners"]["claude"] == {
        "binary": "/fake/bin/claude", "resolved": True, "source": "candidate",
        "env_var": "CLAUDE_BIN", "candidates": ["/fake/candidates/claude"], "executed": False}
    codex = output["runners"]["codex"]
    assert codex["resolved"] is False and codex["binary"] is None and codex["source"] == "unresolved"
    assert any("nothing was executed" in line for line in output["limitations"])


def test_runner_receipt_never_exports_non_scalar_fields(tmp_path):
    def odd(provider):
        return {"binary": {"path": "SECRET"}, "source": ["x"], "env_var": None,
                "candidates": [{"k": "SECRET"}]}
    output = agent_doctor.inventory(claude_registry_paths=[], codex_automation_dir=tmp_path / "codex",
                                    runner_resolver=odd)
    for runner in output["runners"].values():
        assert runner["binary"] is None and runner["resolved"] is False
        assert runner["source"] is None and runner["candidates"] == []
    assert "SECRET" not in json.dumps(output)


def test_cli_prints_runner_resolution(tmp_path, monkeypatch, capsys):
    fake = inspect(tmp_path, [])
    monkeypatch.setattr(agent_doctor, "inventory", lambda: fake)
    assert agent_doctor.main([]) == 0
    text = capsys.readouterr().out
    assert "runner claude: /fake/bin/claude" in text
    assert "runner codex: UNRESOLVED" in text and "not executed" in text
