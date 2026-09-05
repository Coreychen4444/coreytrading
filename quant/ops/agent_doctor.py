"""Inspect persisted scheduler metadata without changing or running any task.

Only allowlisted metadata is exported; embedded prompt fields are discarded.
Task definition files, auth files and trading state are never opened. Registry files describe persisted
entries; only a separate live app listing can prove current registration.

The ``runners`` section resolves the Claude / Codex CLI binaries by path only
(: Codex was not on PATH and lived inside ChatGPT.app). Nothing is
executed, so a resolved path proves presence, not login, version or flags.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import date, datetime, timezone
import json
import tomllib

from quant.core.paths import CLAUDE_SCHEDULE_REGISTRY_ROOT, CODEX_AUTOMATION_ROOT
from quant.ops.agent_runners import resolve_binary

RUNNER_PROVIDERS = ("claude", "codex")

MAX_METADATA_BYTES = 2_000_000
_CLAUDE_FIELDS = {
    "name": ("name",), "id": ("id", "taskId"), "enabled": ("enabled",),
    "model": ("model",), "cron": ("cronExpression", "cron"),
    "filePath": ("filePath",), "cwd": ("cwd",), "lastRunAt": ("lastRunAt",),
}
_CODEX_FIELDS = {
    "name": ("name",), "id": ("id",), "status": ("status",), "kind": ("kind",),
    "model": ("model",), "cwd": ("cwd", "cwds"),
    "projectId": ("projectId", "project_id"), "project": ("project",),
    "rrule": ("rrule",), "cron": ("cron", "cronExpression"),
    "timezone": ("timezone", "timeZone"),
    "executionEnvironment": ("executionEnvironment", "execution_environment"),
    "lastRunAt": ("lastRunAt", "last_run_at"),
}


def _scalar(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if type(value) is list and all(type(item) is str for item in value):
        return list(value)
    return None


def _metadata(row: dict, fields: dict) -> dict:
    # Never copy entire project/config objects: nested content is not metadata.
    output = {}
    for target, aliases in fields.items():
        for alias in aliases:
            if alias in row:
                output[target] = _scalar(row[alias])
                break
    return output


def _read_metadata(path: Path, expected_name: str, parser) -> tuple[object | None, dict]:
    source = {"path": str(path), "exists": path.exists(), "parsed": False}
    if path.name != expected_name or path.is_symlink() or path.parent.is_symlink():
        return None, {**source, "error": "unsupported_metadata_path"}
    if not source["exists"]:
        return None, source
    try:
        if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
            return None, {**source, "error": "unsupported_metadata_size_or_type"}
        value = parser(path.read_text(encoding="utf-8"))
        return value, {**source, "parsed": True}
    except (OSError, ValueError, TypeError):
        # Parser exceptions can contain source fragments; never export them.
        return None, {**source, "error": "metadata_read_or_parse_failed"}


def _claude_rows(document) -> list[dict] | None:
    container = document
    if type(document) is dict:
        known = [key for key in ("scheduledTasks", "tasks", "scheduled_tasks") if key in document]
        if len(known) > 1:
            return None
        if known:
            container = document[known[0]]
        elif {"id", "cronExpression", "enabled"} <= set(document):
            return [document]
        else:
            # A bare ID -> task map is accepted only when every row is a task.
            if document and all(type(row) is dict and ("cronExpression" in row or "filePath" in row)
                                for row in document.values()):
                container = document
            else:
                return None
    if type(container) is list and all(type(row) is dict for row in container):
        return container
    if type(container) is dict and all(type(row) is dict for row in container.values()):
        return [{"id": key, **row} for key, row in container.items()]
    return None


def runner_inventory(resolver=None) -> dict:
    """Path-only CLI resolution per provider; never executes a binary.

    ``resolved=False`` means every runner endpoint of that provider's profile
    (primary and fallback share the runtime) is unavailable, so its roles fail
    closed and cross-provider review cannot approve card writes.
    """
    resolve = resolver or resolve_binary
    out = {}
    for provider in RUNNER_PROVIDERS:
        receipt = resolve(provider)
        binary, source = receipt.get("binary"), receipt.get("source")
        env_var, candidates = receipt.get("env_var"), receipt.get("candidates")
        # Strings only: a receipt is a path inventory, never a nested object.
        out[provider] = {"binary": binary if isinstance(binary, str) else None,
                         "resolved": isinstance(binary, str),
                         "source": source if isinstance(source, str) else None,
                         "env_var": env_var if isinstance(env_var, str) else None,
                         "candidates": ([c for c in candidates if isinstance(c, str)]
                                        if isinstance(candidates, list) else []),
                         "executed": False}
    return out


def inventory(*, claude_registry_paths=None, codex_automation_dir=None, runner_resolver=None) -> dict:
    """Return metadata from exact registry/config filenames, without live claims.

    Paths are injectable for offline tests. An explicit empty Claude path list
    suppresses default discovery. Codex TOML presence is configuration evidence,
    never a registered/running assertion. Timestamps alone do not prove success.
    ``runner_resolver`` injects a fake CLI resolver; the default only stats paths.
    """
    paths = (sorted(CLAUDE_SCHEDULE_REGISTRY_ROOT.glob("*/*/scheduled-tasks.json"))
             if claude_registry_paths is None else [Path(path) for path in claude_registry_paths])
    codex_dir = Path(codex_automation_dir) if codex_automation_dir is not None else CODEX_AUTOMATION_ROOT
    claude = {"sources": [], "tasks": [], "runtime_verified": False}
    codex = {"directory": str(codex_dir), "directory_exists": codex_dir.is_dir(),
             "sources": [], "tasks": [], "runtime_verified": False}
    for path in paths:
        document, source = _read_metadata(path, "scheduled-tasks.json", json.loads)
        claude["sources"].append(source)
        if not source["parsed"]:
            continue
        rows = _claude_rows(document)
        if rows is None:
            source["error"] = "unknown_registry_shape"
            continue
        for row in rows:
            metadata = _metadata(row, _CLAUDE_FIELDS)
            if not metadata.get("id") and not metadata.get("name") and not metadata.get("filePath"):
                continue
            if type(metadata.get("enabled")) is not bool:
                metadata["enabled"] = None
            claude["tasks"].append({**metadata, "source_file": str(path),
                "registry_entry_present": True, "runtime_registered": None,
                "last_run_evidence": "timestamp_only" if metadata.get("lastRunAt") is not None else "unavailable"})
    for path in sorted(codex_dir.glob("*/automation.toml")):
        document, source = _read_metadata(path, "automation.toml", tomllib.loads)
        codex["sources"].append(source)
        if not source["parsed"] or type(document) is not dict:
            continue
        metadata = _metadata(document, _CODEX_FIELDS)
        metadata.setdefault("id", path.parent.name)
        status = metadata.get("status")
        configured_enabled = True if status == "ACTIVE" else False if status == "PAUSED" else None
        codex["tasks"].append({**metadata, "source_file": str(path),
            "configuration_present": True, "configured_enabled": configured_enabled,
            "runtime_registered": None,
            "last_run_evidence": "timestamp_only" if metadata.get("lastRunAt") is not None else "unavailable"})
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "claude": claude, "codex": codex,
            "runners": runner_inventory(runner_resolver),
            "limitations": [
                "Runner binaries were resolved by path in this process's environment only: nothing was executed, login/version/flags stay unverified, and a desktop task's PATH can differ.",
                "Persisted Claude registry entries are not a live app registration check; duplicate session registries remain separate.",
                "Codex automation.toml files show configuration only; ACTIVE does not prove scheduler registration or execution.",
                "enabled/status, file presence and lastRunAt are separate observations; timestamps do not establish successful completion.",
                "Embedded prompt fields were discarded; standalone task definitions, credentials and trading state were not opened.",
            ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="export allowlisted metadata as JSON")
    args = parser.parse_args(argv)
    output = inventory()
    if args.json:
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    else:
        for host in ("claude", "codex"):
            tasks = output[host]["tasks"]
            print(f"{host}: {len(tasks)} persisted entries; live registration unverified")
            for task in tasks:
                enabled = task.get("enabled", task.get("configured_enabled"))
                print(f"  {task.get('name') or task.get('id')}: enabled={enabled}, model={task.get('model')}, lastRunAt={task.get('lastRunAt')}")
        for provider, runner in output["runners"].items():
            print(f"runner {provider}: {runner['binary'] or 'UNRESOLVED'} (source={runner['source']}; path check only, not executed)")
        print("Read-only metadata inventory. Configuration does not establish current execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
