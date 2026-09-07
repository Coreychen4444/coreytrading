"""Inference isolation, bounded processes and honest provider-result parsing."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

import pytest

from quant.ops import agent_runners as ar


SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}
PAYLOAD = {"summary": "frozen evidence analysis"}


def claude_result(**over):
    result = {"type": "result", "subtype": "success", "is_error": False,
              "structured_output": PAYLOAD, "usage": {"input_tokens": 123}}
    result.update(over)
    return json.dumps(result)


def codex_result(*extra):
    return "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message",
         "text": json.dumps(PAYLOAD)}},
        *extra,
        {"type": "turn.completed", "usage": {"input_tokens": 456}},
    ])


@pytest.fixture
def fake_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, "_binary", lambda provider: f"/fake/{provider}")

    def capture(argv, prompt, cwd, env, timeout_s, limit=ar.MAX_OUTPUT_BYTES, on_spawn=None):
        calls.append((argv, prompt, cwd, env))
        if "--help" in argv:
            return 0, " ".join(ar.CLAUDE_REQUIRED_FLAGS + ar.CODEX_REQUIRED_FLAGS
                               + ("--model", "--effort", "--config")), ""
        if "--version" in argv:
            return 0, "2.1.252 (test runner)", ""
        return 0, claude_result(), ""

    monkeypatch.setattr(ar, "_capture", capture)
    return calls


def test_claude_has_no_tools_or_host_customizations_and_reuses_native_auth(fake_cli, tmp_path,
                                                                       monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://untrusted.invalid")
    monkeypatch.setenv("BASH_ENV", "/untrusted/hook")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-value")
    monkeypatch.setenv("CLAUDECODE", "nested")
    result = ar.run("claude", "frozen evidence", SCHEMA, model="user-selected", cwd=tmp_path)
    assert result["status"] == "success"
    assert result["payload"] == PAYLOAD and result["usage"] == {"input_tokens": 123}
    argv, prompt, cwd, env = fake_cli[-1]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert {"--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence", "--no-chrome"} <= set(argv)
    assert "--bare" not in argv and "bypassPermissions" not in argv
    assert "frozen evidence" not in argv and prompt == "frozen evidence"
    assert cwd.parent == tmp_path and not cwd.exists()
    assert env["HOME"] == os.environ["HOME"]
    assert not {"ANTHROPIC_BASE_URL", "BASH_ENV", "TELEGRAM_BOT_TOKEN", "CLAUDECODE"} & env.keys()


@pytest.mark.parametrize("provider,effort", [("claude", "xhigh"), ("codex", "high")])
def test_effort_is_explicit_and_inherited_model_controls_are_removed(
        provider, effort, monkeypatch, fake_cli, tmp_path):
    for name in ("CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_MODEL", "MAX_THINKING_TOKENS",
                 "CODEX_MODEL", "MODEL_REASONING_EFFORT", "CODEX_REASONING_EFFORT"):
        monkeypatch.setenv(name, "untrusted-override")
    seen = {}

    def codex(binary, prompt, schema, model, isolated, env, timeout_s, **kwargs):
        seen.update(kwargs=kwargs, env=env)
        return 0, codex_result(), ""

    monkeypatch.setattr(ar, "_run_codex", codex)
    model = "claude-fable-5-1" if provider == "claude" else "gpt-6-astra"
    result = ar.run(provider, "frozen evidence", SCHEMA, model=model, effort=effort, cwd=tmp_path)
    assert result["status"] == "success"
    assert result["model"] == result["requested_model"] == model
    assert result["requested_effort"] == effort
    assert result["reported_model"] is None and result["reported_models"] == []
    assert result["model_verification"] == "unreported"
    if provider == "claude":
        argv, _, _, env = fake_cli[-1]
        assert argv[argv.index("--effort") + 1] == effort
        assert argv[argv.index("--model") + 1] == model
    else:
        assert seen["kwargs"]["effort"] == effort
        env = seen["env"]
    assert "untrusted-override" not in env.values()


@pytest.mark.parametrize("provider,effort", [("claude", "extra"), ("claude", "minimal"),
                                          ("codex", "ultra"), ("codex", "HIGH"),
                                          ("codex", False), ("claude", {})])
def test_unsupported_effort_fails_before_any_process(provider, effort, monkeypatch):
    monkeypatch.setattr(ar, "_binary", lambda *args: pytest.fail("invalid effort launched a probe"))
    result = ar.run(provider, "evidence", SCHEMA, effort=effort)
    assert result["status"] == "failed" and result["error"] == "invalid_effort"


@pytest.mark.parametrize("provider,flag,setting", [("claude", "--effort", {"effort": "xhigh"}),
                                                 ("claude", "--model", {"model": "claude-opus-5"}),
                                                 ("codex", "--config", {"effort": "high"}),
                                                 ("codex", "--model", {"model": "gpt-6-astra"})])
def test_optional_controls_require_cli_support_only_when_requested(
        provider, flag, setting, monkeypatch, fake_cli, tmp_path):
    original = ar._capture

    def capture(argv, *args, **kwargs):
        code, stdout, stderr = original(argv, *args, **kwargs)
        return code, stdout.replace(flag, "") if "--help" in argv else stdout, stderr

    monkeypatch.setattr(ar, "_capture", capture)
    monkeypatch.setattr(ar, "_run_codex", lambda *args: (0, codex_result(), ""))
    assert ar.run(provider, "evidence", SCHEMA, cwd=tmp_path)["status"] == "success"
    fake_cli.clear()
    result = ar.run(provider, "evidence", SCHEMA, cwd=tmp_path, **setting)
    assert result["status"] == "unavailable"
    assert result["error"] == ("model_flag_unsupported" if flag == "--model" else "effort_flag_unsupported")
    assert not any(call[1] for call in fake_cli)


@pytest.mark.parametrize("role", ["macro", "technical", "risk", "valuation"])
def test_claude_schema_projects_only_wire_constraints_and_keeps_authority(role):
    from quant.core.agent_contracts import result_schema

    original = result_schema(role)
    before = json.dumps(original, sort_keys=True)
    argv = ar._claude_argv("/fake/claude", original, None)
    projected = json.loads(argv[argv.index("--json-schema") + 1])
    assert "$schema" not in projected
    assert projected["required"] == original["required"]
    assert projected["additionalProperties"] is False
    assert projected["properties"]["authority"] == original["properties"]["authority"]
    text = projected["properties"]["summary"]
    assert "minLength" not in text and "maxLength" not in text
    assert '"minLength": 1' in text["description"]
    assert '"maxLength": 12000' in text["description"]
    assert "maxItems" not in projected["properties"]["observations"]
    assert projected["properties"]["observations"]["minItems"] == 0
    assert json.dumps(original, sort_keys=True) == before


def test_claude_schema_rejects_unknown_keywords_without_dropping_named_properties():
    schema = {"type": "object", "properties": {"maxLength": {"type": "string"}},
              "required": ["maxLength"], "additionalProperties": False}
    assert "maxLength" in ar._claude_schema(schema)["properties"]
    with pytest.raises(ar.RunnerError, match="claude_schema_keyword_unsupported"):
        ar._claude_schema({**schema, "unevaluatedProperties": False})


@pytest.mark.parametrize("field,value", [("summary", ""), ("summary", "x" * 12001),
                                        ("unknowns", ["synthetic"] * 101)])
def test_projected_claude_output_still_fails_the_full_host_contract(
        field, value, monkeypatch, fake_cli, tmp_path):
    from quant.core.agent_contracts import CONTRACT_VERSION, RESULT_SCHEMA_VERSION
    from quant.ops import agent_runtime as runtime
    from quant.ops.agent_snapshots import make_snapshot

    monkeypatch.setattr(runtime, "policy_context", lambda role: {"synthetic": "test only"})
    capture = ar._capture

    def invalid_output(argv, prompt, cwd, env, timeout_s, limit=ar.MAX_OUTPUT_BYTES, on_spawn=None):
        if "--print" not in argv:
            return capture(argv, prompt, cwd, env, timeout_s, limit)
        packet = json.loads(prompt.split("\n", 1)[1])
        # The transport projection never replaces the complete prompt contract.
        assert packet["output_schema"]["properties"]["summary"]["maxLength"] == 12000
        snapshot = packet["snapshot"]
        payload = {"schema_version": RESULT_SCHEMA_VERSION, "contract_version": CONTRACT_VERSION,
                   "valuation_scenarios": [], "role": "macro", "snapshot_id": snapshot["snapshot_id"],
                   "input_hash": snapshot["input_hash"], "generated_at": snapshot["as_of"],
                   "expires_at": snapshot["expires_at"], "summary": "synthetic",
                   "observations": [], "hypotheses": [], "risks": [], "unknowns": [],
                   "proposal": None, "requests": [], "review": None,
                   "authority": {"mode": "analysis_only", "may_write_state": False,
                                 "may_place_orders": False}, field: value}
        return 0, claude_result(structured_output=payload), ""

    monkeypatch.setattr(ar, "_capture", invalid_output)
    routes = runtime.load_routes()
    routes["roles"]["macro"].update(provider="claude", fallback=[])
    result = runtime.run_role("macro", "synthetic only", make_snapshot({"synthetic": 123}),
                              routes=routes, run_dir=tmp_path, control_dir=tmp_path / "control", runner=ar.run)
    assert result["status"] == "failed" and result["result"] is None
    assert result["attempts"][0]["status"] == "invalid_result"
    assert f"result.{field}:" in result["attempts"][0]["error"]


def test_missing_required_flag_fails_before_model(monkeypatch, fake_cli, tmp_path):
    monkeypatch.setattr(ar, "_capture", lambda *args: (0, "2.1.0 --tools", ""))
    result = ar.run("claude", "evidence", SCHEMA, cwd=tmp_path)
    assert result["status"] == "unavailable"
    assert result["error"] == "tool_free_isolation_unsupported"


def test_missing_runner_and_unsupported_provider(monkeypatch):
    monkeypatch.setattr(ar, "_binary", lambda p: None)
    assert ar.run("claude", "evidence", SCHEMA)["status"] == "unavailable"
    assert ar.run("unknown", "evidence", SCHEMA)["error"] == "unsupported_provider"


def test_nonzero_exit_does_not_echo_secret_stderr(monkeypatch, fake_cli, tmp_path):
    original = ar._capture

    def capture(argv, *args, **kwargs):
        if "--print" in argv:
            return 42, claude_result(), "Authorization: secret-token"
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(ar, "_capture", capture)
    result = ar.run("claude", "evidence", SCHEMA, cwd=tmp_path)
    assert result["status"] == "failed" and result["payload"] is None
    assert result["error"] == "provider_exit_42"
    assert "secret-token" not in json.dumps(result)


@pytest.mark.parametrize("text", ["plain prose", '[]', '{"type":"result"}',
                                 claude_result(structured_output=[]),
                                 '{"type":"result","type":"result"}',
                                 claude_result(structured_output={"n": float("nan")})])
def test_claude_rejects_malformed_wrappers(text):
    with pytest.raises(ar.RunnerError):
        ar._parse_claude(text)


@pytest.mark.parametrize("over", [{"subtype": "error_max_turns"}, {"is_error": True},
                                  {"permission_denials": [{"tool_name": "Bash"}]}])
def test_claude_failures_do_not_masquerade_as_json_success(over):
    with pytest.raises(ar.RunnerError):
        ar._parse_claude(claude_result(**over))


def test_claude_result_text_fallback_and_usage_filter():
    payload, usage = ar._parse_claude(claude_result(
        structured_output=None, result=json.dumps(PAYLOAD),
        usage={"output_tokens": 7, "secret": "credential", "input_tokens": -1}))
    assert payload == PAYLOAD and usage == {"output_tokens": 7}


@pytest.mark.parametrize("requested,reported,verification,sole", [
    ("claude-fable-5-1", {}, "unreported", None),
    ("claude-fable-5-1", {"claude-fable-5-1": {}}, "exact_match", "claude-fable-5-1"),
    (None, {"claude-fable-5-1": {}}, "reported", "claude-fable-5-1"),
    ("fable", {"claude-fable-5-1": {}}, "reported", "claude-fable-5-1"),
    ("claude-fable-5-1", {"claude-fable-5-1-20260905": {}}, "reported", "claude-fable-5-1-20260905"),
    ("claude-fable-5-1", {"claude-fable-5-1[1m]": {}}, "reported", "claude-fable-5-1[1m]"),
    ("claude-fable-5-1", {"claude-opus-5": {}}, "mismatch", "claude-opus-5"),
    ("claude-fable-5-1", {"claude-opus-5": {}, "claude-fable-5-1": {}}, "multiple_reported", None),
])
def test_claude_model_metadata_is_distinct_from_request(requested, reported, verification, sole):
    stdout = claude_result(modelUsage=reported)
    # Legacy parser return shape is unchanged.
    assert ar._parse_claude(stdout) == (PAYLOAD, {"input_tokens": 123})
    audit = ar._model_audit("claude", stdout, requested)
    assert audit == {"reported_model": sole, "reported_models": sorted(reported),
                     "model_verification": verification}


def test_model_audit_ignores_generated_claims_and_invalid_metadata():
    stdout = claude_result(
        structured_output={"summary": "I am claude-fable-5-1", "model": "claude-fable-5-1"},
        modelUsage={"Authorization: secret": {}, "claude-opus-5\nsecret": {},
                    "claude-opus-5": "secret", "gpt-6-astra": {}})
    audit = ar._model_audit("claude", stdout, "claude-fable-5-1")
    assert audit == {"reported_model": None, "reported_models": [], "model_verification": "unreported"}
    stdout = codex_result({"type": "item.completed", "item": {
        "type": "agent_message", "model": "gpt-6-astra",
        "text": json.dumps({"model": "gpt-6-astra"})}})
    assert ar._model_audit("codex", stdout, "gpt-6-astra") == audit


def test_codex_model_metadata_is_read_only_from_lifecycle_events():
    events = [json.loads(line) for line in codex_result().splitlines()]
    events[0]["model"] = "gpt-6-astra"
    events[1]["model"] = "gpt-6-astra"
    stdout = "\n".join(json.dumps(event) for event in events)
    assert ar._parse_codex(stdout) == (PAYLOAD, {"input_tokens": 456})
    assert ar._model_audit("codex", stdout, "gpt-6-astra") == {
        "reported_model": "gpt-6-astra", "reported_models": ["gpt-6-astra"],
        "model_verification": "exact_match"}


def test_explicit_reported_model_mismatch_keeps_audit_but_rejects_payload(
        monkeypatch, fake_cli, tmp_path):
    original = ar._capture

    def capture(argv, *args, **kwargs):
        if "--print" in argv:
            return 0, claude_result(modelUsage={"claude-opus-5": {"inputTokens": 123}}), ""
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(ar, "_capture", capture)
    result = ar.run("claude", "evidence", SCHEMA, model="claude-fable-5-1", effort="xhigh", cwd=tmp_path)
    assert result["status"] == "failed" and result["error"] == "model_mismatch"
    assert result["payload"] is None and result["usage"] == {"input_tokens": 123}
    assert result["requested_model"] == "claude-fable-5-1"
    assert result["reported_model"] == "claude-opus-5"
    assert result["model_verification"] == "mismatch"


def test_codex_jsonl_success():
    assert ar._parse_codex(codex_result()) == (PAYLOAD, {"input_tokens": 456})


def test_codex_accepts_only_expected_disabled_code_host_notice_before_turn():
    notice = {"type": "item.completed", "item": {"type": "error", "message":
              ar.CODEX_DISABLED_HOST_NOTICE + " enable the host to use Code Mode."}}
    events = codex_result().splitlines()
    events.insert(1, json.dumps(notice))
    assert ar._parse_codex("\n".join(events))[0] == PAYLOAD
    # Same text during a turn is not a harmless startup notice.
    with pytest.raises(ar.RunnerError):
        ar._parse_codex(codex_result(notice))
    notice["item"]["message"] = "Authentication failed"
    events[1] = json.dumps(notice)
    with pytest.raises(ar.RunnerError):
        ar._parse_codex("\n".join(events))


@pytest.mark.parametrize("event", [
    {"type": "turn.failed", "error": {"message": "secret"}},
    {"type": "item.started", "item": {"type": "command_execution"}},
    {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
    {"type": "item.completed", "item": {"type": "file_change"}},
])
def test_codex_rejects_tool_events_or_turn_failure(event):
    with pytest.raises(ar.RunnerError):
        ar._parse_codex(codex_result(event))


def test_codex_requires_completed_turn_and_json_object():
    for text in ('{"type":"turn.started"}',
                 '{"type":"turn.completed"}\n',
                 codex_result({"type": "item.completed", "item": {
                     "type": "agent_message", "text": "not JSON"}})):
        with pytest.raises(ar.RunnerError):
            ar._parse_codex(text)


def test_codex_runs_through_adapter_and_parses_output(monkeypatch, fake_cli, tmp_path):
    calls = []

    def codex(*args):
        calls.append(args)
        return 0, codex_result(), ""

    monkeypatch.setattr(ar, "_run_codex", codex)
    result = ar.run("codex", "frozen evidence", SCHEMA, cwd=tmp_path)
    assert result["status"] == "success" and result["payload"] == PAYLOAD
    assert calls[0][1:4] == ("frozen evidence", SCHEMA, None)


def test_codex_flags_remove_active_tool_integrations(tmp_path):
    argv = ar._codex_argv("/runner", tmp_path / "schema", tmp_path, "chosen-model", "high")
    for feature in {"shell_tool", "unified_exec", "apps", "plugins", "hooks",
                    "browser_use", "computer_use", "code_mode_host", "view_image"}:
        assert ["--disable", feature] == argv[argv.index(feature)-1:argv.index(feature)+1]
    assert 'web_search="disabled"' in argv and 'mcp_servers={}' in argv
    assert 'features.skip_host_skill_discovery=true' in argv
    assert 'project_doc_max_bytes=0' in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert argv[argv.index("--model") + 1] == "chosen-model"
    assert 'model_reasoning_effort="high"' in argv
    assert argv[argv.index('model_reasoning_effort="high"') - 1] == "--config"


def test_codex_auth_is_symlink_and_network_only_proxy(monkeypatch, tmp_path):
    personal = tmp_path / "personal"
    (personal / ".codex").mkdir(parents=True)
    auth = personal / ".codex/auth.json"
    auth.write_text("test credential fixture")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: personal))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    seen = {}

    def capture(argv, prompt, cwd, env, timeout):
        seen.update(argv=argv, env=env)
        link = Path(env["CODEX_HOME"]) / "auth.json"
        assert link.is_symlink() and link.resolve() == auth
        assert env["HTTP_PROXY"].startswith("http://127.0.0.1:")
        assert "test credential fixture" not in json.dumps([argv, env])
        return 0, codex_result(), ""

    monkeypatch.setattr(ar, "_capture", capture)
    ar._run_codex("/fake/codex", "evidence", SCHEMA, None, scratch, {}, 10)
    profile = seen["argv"][2]
    assert "deny file-read-data" in profile and "deny file-write*" in profile
    assert "deny process-exec" in profile and "deny network-outbound" in profile
    assert auth.read_text() == "test credential fixture"


@pytest.mark.parametrize("target", ["api.telegram.org:443", "localhost:11111", "chatgpt.com:80",
                                    "chatgpt.com.evil.invalid:443"])
def test_proxy_denies_non_model_destinations_without_connecting(monkeypatch, target):
    original = socket.create_connection
    with ar._model_proxy() as port:
        client = original(("127.0.0.1", port), timeout=2)
        with client:
            monkeypatch.setattr(socket, "create_connection", lambda *a, **k:
                                pytest.fail("forbidden upstream connection"))
            client.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            assert client.recv(1024).startswith(b"HTTP/1.1 403")


def test_proxy_passes_tls_bytes_only_to_an_allowed_model_host(monkeypatch):
    original = socket.create_connection
    relay, provider = socket.socketpair()
    destinations = []

    def upstream(address, timeout):
        destinations.append(address)
        return relay

    with ar._model_proxy() as port, provider:
        with original(("127.0.0.1", port), timeout=2) as client:
            monkeypatch.setattr(socket, "create_connection", upstream)
            client.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\n\r\n")
            assert client.recv(1024).startswith(b"HTTP/1.1 200")
            client.sendall(b"opaque-TLS-record")
            provider.settimeout(2)
            assert provider.recv(1024) == b"opaque-TLS-record"
            provider.sendall(b"opaque-provider-response")
            assert client.recv(1024) == b"opaque-provider-response"
    assert destinations == [("chatgpt.com", 443)]


def test_capture_bounds_stdout_and_stderr(tmp_path):
    with pytest.raises(ar.RunnerError, match="output_limit_exceeded"):
        ar._capture([sys.executable, "-c", "import os; os.write(2, b'x'*10000)"],
                    "", tmp_path, ar._environment(), 5, limit=100)


def test_capture_timeout_kills_owned_group_including_descendants(monkeypatch, tmp_path):
    called = []
    real = os.killpg

    def kill(pid, sig):
        called.append((pid, sig))
        return real(pid, sig)

    monkeypatch.setattr(os, "killpg", kill)
    code = "import subprocess; subprocess.Popen(['/bin/sleep','20'])"
    with pytest.raises(ar.RunnerError, match="timeout"):
        ar._capture([sys.executable, "-c", code], "", tmp_path, ar._environment(), 0.2)
    assert called and called[0][1] == signal.SIGKILL


def test_capture_feeds_large_stdin_while_draining_output(tmp_path):
    code = "import sys; sys.stdout.write('x'*100000); sys.stdout.flush(); print(len(sys.stdin.read()))"
    status, stdout, stderr = ar._capture([sys.executable, "-c", code], "p" * 200000,
                                         tmp_path, ar._environment(), 5)
    assert status == 0 and stdout.endswith("200000\n") and not stderr


def test_capture_registers_owned_group_before_delivering_prompt(tmp_path):
    marker = tmp_path / "registered"
    groups = []

    def registered(pid):
        assert os.getpgid(pid) == pid
        groups.append(pid)
        marker.write_text("registered")

    code = ("import sys,pathlib; text=sys.stdin.read(); "
            "assert pathlib.Path('registered').read_text() == 'registered'; print(text)")
    result = ar._capture([sys.executable, "-c", code], "synthetic prompt", tmp_path,
                         ar._environment(), 5, on_spawn=registered)
    assert groups and result == (0, "synthetic prompt\n", "")
    with pytest.raises(ProcessLookupError):
        os.killpg(groups[0], 0)


def test_capture_registration_failure_kills_group_without_delivering_prompt(tmp_path):
    groups = []

    def rejected(pid):
        groups.append(pid)
        raise RuntimeError("secret-registration-error")

    code = ("import sys,pathlib; text=sys.stdin.read(); "
            "pathlib.Path('received').write_text(text)")
    with pytest.raises(ar.RunnerError, match="^process_registration_failed$"):
        ar._capture([sys.executable, "-c", code], "synthetic prompt", tmp_path,
                    ar._environment(), 5, on_spawn=rejected)
    assert not (tmp_path / "received").exists()
    with pytest.raises(ProcessLookupError):
        os.killpg(groups[0], 0)


@pytest.mark.parametrize("provider,expected_probes", [("claude", 2), ("codex", 3)])
def test_runner_forwards_registration_to_every_probe_and_inference(monkeypatch, tmp_path,
                                                                 provider, expected_probes):
    seen = []
    callback = lambda pid: None
    monkeypatch.setattr(ar, "_binary", lambda name: f"/fake/{name}")

    def capture(argv, prompt, cwd, env, timeout, limit=ar.MAX_OUTPUT_BYTES, on_spawn=None):
        assert on_spawn is callback
        seen.append(prompt)
        if "--help" in argv:
            return 0, " ".join(ar.CLAUDE_REQUIRED_FLAGS + ar.CODEX_REQUIRED_FLAGS), ""
        if "--version" in argv:
            return 0, "1.2.3", ""
        return 0, claude_result(), ""

    def codex(*args, on_spawn=None):
        assert on_spawn is callback
        seen.append(args[1])
        return 0, codex_result(), ""

    monkeypatch.setattr(ar, "_capture", capture)
    monkeypatch.setattr(ar, "_run_codex", codex)
    assert ar.run(provider, "synthetic prompt", SCHEMA, cwd=tmp_path,
                  on_spawn=callback)["status"] == "success"
    assert seen == [""] * expected_probes + ["synthetic prompt"]


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="macOS seatbelt integration test")
def test_real_os_sandbox_blocks_private_read_external_write_and_exec(tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("canary-secret")
    auth = tmp_path / "auth.json"
    auth.write_text("fake-auth")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside.txt"
    script = (f'if IFS= read -r value < "{private}"; then echo LEAK; fi\n'
              f'if echo bad > "{outside}"; then echo WRITE; fi\n'
              f'if echo bad > "{auth}"; then echo AUTH_WRITE; fi\n'
              'if /usr/bin/true; then echo EXEC; fi\n'
              'echo scratch > allowed.txt\n')
    profile = ar._sandbox_profile("/bin/bash", scratch, auth, 9)
    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/bin/bash"],
                            input=script, text=True, cwd=scratch, capture_output=True, timeout=5)
    assert "LEAK" not in result.stdout and "WRITE" not in result.stdout
    assert "EXEC" not in result.stdout and not outside.exists()
    assert auth.read_text() == "fake-auth"
    assert (scratch / "allowed.txt").read_text() == "scratch\n"


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="macOS seatbelt integration test")
def test_real_os_sandbox_blocks_broker_loopback(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        profile = ar._sandbox_profile("/usr/bin/nc", tmp_path, tmp_path / "auth", port + 1)
        result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/nc",
                                 "-z", "-w", "1", "127.0.0.1", str(port)],
                                capture_output=True, timeout=5)
        assert result.returncode != 0
        allowed = ar._sandbox_profile("/usr/bin/nc", tmp_path, tmp_path / "auth", port)
        result = subprocess.run(["/usr/bin/sandbox-exec", "-p", allowed, "/usr/bin/nc",
                                 "-z", "-w", "1", "127.0.0.1", str(port)],
                                capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="macOS Codex local configuration probe")
def test_installed_codex_accepts_isolated_config_without_model_request(tmp_path):
    binary = ar._binary("codex")
    if not binary:
        pytest.skip("Codex is not installed")
    home = tmp_path / "codex-home"
    home.mkdir()
    env = ar._environment()
    env.update(CODEX_HOME=str(home), TMPDIR=str(tmp_path))
    argv = ar._codex_argv(binary, tmp_path / "schema", tmp_path, None)
    flags = []
    for index, value in enumerate(argv):
        if value in {"--disable", "--config"}:
            flags.extend(argv[index:index + 2])
    profile = ar._sandbox_profile(binary, tmp_path, home / "auth.json", 9)
    code, stdout, _ = ar._capture(["/usr/bin/sandbox-exec", "-p", profile, binary,
                                  *flags, "features", "list"], "", tmp_path, env, 10)
    assert code == 0
    features = {line.split()[0]: line.split()[-1] for line in stdout.splitlines() if line.strip()}
    # unified_exec is normalized true in 0.153.1 even with --disable; the
    # shell_tool gate and real OS deny tests above enforce the boundary.
    for key in {"shell_tool", "apps", "plugins", "hooks",
                "browser_use", "computer_use", "code_mode_host", "view_image"}:
        assert features.get(key) == "false", key


# ── 二进制解析 ( 交接核对: codex 不在定时任务 PATH 上, 住在 ChatGPT.app 里) ──

def test_codex_candidates_include_chatgpt_bundle_after_validated_cache():
    """候选顺序 = 已验证的解析顺序: plugin appserver 缓存副本 (09-05 实际解析到的) 在前,
    ChatGPT.app 内置副本是同一 build 的备用, 独立 Codex.app 最后。"""
    names = [str(p) for p in ar._binary_candidates("codex")]
    cache = next(i for i, n in enumerate(names) if n.endswith(".codex/plugins/.plugin-appserver/codex"))
    bundle = names.index("/Applications/ChatGPT.app/Contents/Resources/codex")
    standalone = names.index("/Applications/Codex.app/Contents/Resources/codex")
    assert cache < bundle < standalone
    assert [str(p) for p in ar._binary_candidates("claude")] == [str(ar.Path.home() / ".local/bin/claude")]


def test_resolve_binary_precedence_is_env_then_path_then_candidates(tmp_path, monkeypatch):
    real = tmp_path / "bundle" / "codex"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(ar.shutil, "which", lambda name: None)
    monkeypatch.setattr(ar, "_binary_candidates", lambda p: [tmp_path / "missing" / "codex", real])
    receipt = ar.resolve_binary("codex")
    assert receipt["binary"] == str(real.resolve()) and receipt["source"] == "candidate"
    assert receipt["candidates"] == [str(tmp_path / "missing" / "codex"), str(real)]
    assert receipt["env_var"] == "CODEX_BIN" and receipt["provider"] == "codex"
    assert ar._binary("codex") == str(real.resolve())

    on_path = tmp_path / "path" / "codex"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)
    monkeypatch.setattr(ar.shutil, "which", lambda name: str(on_path))
    via_path = ar.resolve_binary("codex")
    assert via_path["source"] == "path" and via_path["binary"] == str(on_path.resolve())

    monkeypatch.setenv("CODEX_BIN", str(real))
    via_env = ar.resolve_binary("codex")
    assert via_env["source"] == "env" and via_env["binary"] == str(real.resolve())

    monkeypatch.setenv("CODEX_BIN", str(tmp_path / "nope"))
    invalid = ar.resolve_binary("codex")
    assert invalid["binary"] is None and invalid["source"] == "env_invalid", \
        "显式覆盖无效 = fail closed, 不回落到 PATH"
    assert ar._binary("codex") is None


def test_resolve_binary_reports_unresolved_without_raising(tmp_path, monkeypatch):
    """被沙箱拒绝的位置算"解析不到", 不许把 doctor / runner 炸掉。"""
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(ar.shutil, "which", lambda name: None)
    denied = tmp_path / "denied" / "codex"

    def is_file(self):
        if self == denied:
            raise PermissionError("sandboxed")
        return False

    monkeypatch.setattr(ar.Path, "is_file", is_file)
    monkeypatch.setattr(ar, "_binary_candidates", lambda p: [denied, tmp_path / "absent" / "codex"])
    assert ar.resolve_binary("codex") == {
        "provider": "codex", "binary": None, "source": "unresolved", "env_var": "CODEX_BIN",
        "candidates": [str(denied), str(tmp_path / "absent" / "codex")]}
