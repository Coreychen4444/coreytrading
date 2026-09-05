"""Bounded, tool-free CLI inference adapters for frozen agent evidence.

Authentication stays with each installed CLI; this module never opens credential
files. Claude removes tools. Codex disables integrations and uses an OS sandbox
plus a model-endpoint-only TLS proxy, even if built-in tools remain visible.
Missing isolation fails closed. Domain contracts are validated by the runtime.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager


MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_BYTES = 4 * 1024 * 1024
PROBE_TIMEOUT_S = 10
SUPPORTED_EFFORTS = {
    "claude": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "codex": frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
}
CLAUDE_REQUIRED_FLAGS = (
    "--safe-mode", "--tools", "--strict-mcp-config", "--mcp-config",
    "--disable-slash-commands", "--no-session-persistence", "--json-schema",
    "--setting-sources", "--permission-mode", "--no-chrome",
)
CODEX_REQUIRED_FLAGS = ("--ignore-user-config", "--ignore-rules", "--ephemeral",
                        "--output-schema", "--sandbox", "--json")
CODEX_DISABLED_FEATURES = (
    # 0.153.1 normalizes unified_exec back to true on macOS. shell_tool=false
    # gates command exposure; OS process-exec denial remains authoritative.
    "shell_tool", "unified_exec", "shell_snapshot", "shell_snapshot_v2",
    "apps", "plugins", "hooks", "browser_use", "browser_use_external",
    "in_app_browser", "computer_use", "image_generation", "view_image",
    "code_mode", "code_mode_host", "multi_agent", "multi_agent_v2", "goals",
    "memories", "external_agent_memory_import", "skill_mcp_dependency_install",
    "skill_search", "remote_control", "workspace_dependencies", "tool_suggest",
    "recommended_plugins", "enable_fanout", "enable_mcp_apps",
    "default_mode_request_user_input",
)
MODEL_HOSTS = frozenset({"chatgpt.com", "auth.openai.com", "api.openai.com"})
CODEX_DISABLED_HOST_NOTICE = (
    "Code Mode is unavailable because code-mode host is disabled. "
    "Code mode will fail closed;"
)


class RunnerError(Exception):
    """A public, credential-free failure code, never raw CLI output."""


def _binary_candidates(provider: str) -> list[Path]:
    """Known install locations consulted only when the CLI is not on PATH.

    Order is the validated resolution order; a later entry is a spare, not a
    preference. : on this host the Codex CLI is not on the desktop
    task PATH at all. It ships inside ChatGPT.app, which also copies it into
    the plugin appserver cache (the copy validated first); the app bundle is the
    durable spare when that cache churns. Standalone Codex.app stays last.
    """
    if provider == "claude":
        return [Path.home() / ".local/bin/claude"]
    return [
        Path.home() / ".local/bin/codex",
        Path.home() / ".codex/plugins/.plugin-appserver/codex",
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
    ]


def _executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        # A denied or unreadable location is "not resolvable", never a crash.
        return False


def resolve_binary(provider: str) -> dict:
    """Path-only resolution receipt: explicit env override → PATH → known locations.

    Nothing is executed and no credential is touched, so a resolved path proves
    presence only, not login state, version or flag support (the runner probes
    those before any model call). An explicit ``<PROVIDER>_BIN`` that does not
    point at an executable fails closed instead of falling through to PATH.
    """
    variable = f"{provider.upper()}_BIN"
    candidates = _binary_candidates(provider)
    out = {"provider": provider, "binary": None, "source": None, "env_var": variable,
           "candidates": [str(candidate) for candidate in candidates]}
    explicit = os.environ.get(variable)
    if explicit:
        p = Path(explicit).expanduser()
        if _executable(p):
            return {**out, "binary": str(p.resolve()), "source": "env"}
        return {**out, "source": "env_invalid"}
    found = shutil.which(provider)
    if found:
        return {**out, "binary": str(Path(found).resolve()), "source": "path"}
    for candidate in candidates:
        if _executable(candidate):
            return {**out, "binary": str(candidate.resolve()), "source": "candidate"}
    return {**out, "source": "unresolved"}


def _binary(provider: str) -> str | None:
    return resolve_binary(provider)["binary"]


def _environment() -> dict[str, str]:
    # Keep native OAuth/keychain discovery, not inherited hooks, wrappers,
    # endpoint overrides, shell startup code, proxy credentials, or nested-run
    # control variables. CLI subscription auth therefore stays usable.
    keep = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR",
            "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    env.update({"CLAUDE_CODE_SAFE_MODE": "1", "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
    return env


class _InferenceProxy(socketserver.ThreadingTCPServer):
    """CONNECT-only TLS passthrough: auth is encrypted end to end with provider."""
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self):
        self.connections = set()
        self.connection_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), _Tunnel)

    def handle_error(self, request, client_address):
        # Never print an exception containing network headers or credentials.
        pass

    def close_connections(self):
        with self.connection_lock:
            for conn in self.connections:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class _Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        if self.server.stopping.is_set():
            return
        client = self.request
        client.settimeout(5)
        header = bytearray()
        while b"\r\n\r\n" not in header and len(header) <= 8192:
            part = client.recv(1)
            if not part:
                return
            header.extend(part)
        try:
            first = bytes(header).split(b"\r\n", 1)[0].decode("ascii").split()
            method, target, protocol = first
            host, port = target.rsplit(":", 1)
            allowed = (method == "CONNECT" and protocol in {"HTTP/1.0", "HTTP/1.1"}
                       and host.lower() in MODEL_HOSTS and port == "443"
                       and len(header) <= 8192)
        except (ValueError, UnicodeError):
            allowed = False
        if not allowed:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        with socket.create_connection((host.lower(), 443), timeout=10) as upstream:
            if self.server.stopping.is_set():
                return
            with self.server.connection_lock:
                self.server.connections.update((client, upstream))
            try:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                client.settimeout(10)
                upstream.settimeout(10)
                with selectors.DefaultSelector() as selector:
                    selector.register(client, selectors.EVENT_READ, upstream)
                    selector.register(upstream, selectors.EVENT_READ, client)
                    while True:
                        events = selector.select(120)
                        if not events:
                            return
                        for key, _ in events:
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            key.data.sendall(data)
            finally:
                with self.server.connection_lock:
                    self.server.connections.difference_update((client, upstream))


@contextmanager
def _model_proxy():
    with _InferenceProxy() as server:
        worker = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            yield server.server_address[1]
        finally:
            server.stopping.set()
            server.shutdown()
            server.close_connections()
            worker.join(timeout=2)


def _sandbox_profile(binary: str, isolated: Path, auth_file: Path, port: int) -> str:
    """Deny live data, writes outside scratch, non-runner exec and direct egress.

    Denials use require-not allowlists to avoid ambiguous allow/deny precedence.
    The runner can read its auth for its own HTTPS calls. Model tools that read
    arbitrary files are disabled separately; the live project is never readable.
    """
    quote = lambda p: json.dumps(str(p))
    allowed_reads = [f'(subpath {quote(p)})' for p in (
        "/System", "/usr", "/bin", "/sbin", "/Library/Apple",
        "/Library/Preferences", "/private/etc/ssl", "/private/etc",
        "/dev", str(isolated.resolve()),
    )]
    # dyld reads the root directory during startup even for a native binary.
    allowed_reads += [f'(literal {quote(p)})' for p in ("/", binary, auth_file.resolve())]
    return "\n".join([
        "(version 1)", "(allow default)",
        "(deny file-read-data (require-not (require-any " + " ".join(allowed_reads) + ")))",
        f'(deny file-write* (require-not (require-any (subpath {quote(isolated.resolve())}) '
        '(literal "/dev/null"))))',
        f'(deny process-exec (require-not (literal {quote(binary)})))',
        "(deny network-inbound)",
        f'(deny network-outbound (require-not (remote tcp "localhost:{port}")))',
    ])


def _codex_argv(binary: str, schema_file: Path, isolated: Path,
                model: str | None, effort: str | None = None) -> list[str]:
    argv = [binary, "--ask-for-approval", "never", "exec", "--ignore-user-config",
            "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--sandbox",
            "read-only", "--json", "--color", "never", "--output-schema",
            str(schema_file), "--cd", str(isolated)]
    for feature in CODEX_DISABLED_FEATURES:
        argv.extend(["--disable", feature])
    for config in (
        'web_search="disabled"', 'mcp_servers={}', 'notify=[]',
        'features.skip_host_skill_discovery=true', 'project_doc_max_bytes=0',
        'tools.update_plan.enabled=false',
        'shell_environment_policy.inherit="none"', 'allow_login_shell=false',
        'check_for_update_on_startup=false', 'analytics.enabled=false',
        'feedback.enabled=false', 'history.persistence="none"',
        'cli_auth_credentials_store="file"', 'suppress_unstable_features_warning=true',
    ):
        argv.extend(["--config", config])
    if model:
        argv.extend(["--model", model])
    if effort is not None:
        argv.extend(["--config", f"model_reasoning_effort={json.dumps(effort)}"])
    argv.append("-")
    return argv


def _run_codex(binary: str, prompt: str, schema: dict, model: str | None,
                isolated: Path, env: dict, timeout_s: float, on_spawn=None,
                effort: str | None = None) -> tuple[int, str, str]:
    sandbox = shutil.which("sandbox-exec")
    if not sandbox:
        raise RunnerError("codex_os_sandbox_unavailable")
    auth = Path.home() / ".codex" / "auth.json"
    if not auth.is_file():
        raise RunnerError("codex_subscription_file_auth_unavailable")
    home = isolated / "codex-home"
    home.mkdir(mode=0o700)
    # No credential reads/copies in our process. Refresh may atomically replace
    # the scratch symlink, but OS sandbox forbids changing the original auth.
    (home / "auth.json").symlink_to(auth)
    schema_file = isolated / "output-schema.json"
    schema_file.write_text(json.dumps(schema, ensure_ascii=False, allow_nan=False))
    child_env = dict(env)
    child_env["CODEX_HOME"] = str(home)
    child_env["TMPDIR"] = str(isolated)
    argv = _codex_argv(binary, schema_file, isolated, model, effort)
    with _model_proxy() as port:
        proxy = f"http://127.0.0.1:{port}"
        child_env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy, ALL_PROXY=proxy,
                         https_proxy=proxy, http_proxy=proxy, all_proxy=proxy,
                         NO_PROXY="", no_proxy="")
        profile = _sandbox_profile(binary, isolated, auth, port)
        return _capture([sandbox, "-p", profile, *argv], prompt, isolated,
                        child_env, timeout_s, **({"on_spawn": on_spawn} if on_spawn else {}))


def _kill_group(proc: subprocess.Popen) -> None:
    # Group ownership is established by start_new_session=True. Even if the
    # leader exited, a descendant may retain stdout and must not outlive a run.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def _capture(argv: list[str], prompt: str, cwd: Path, env: dict[str, str],
             timeout_s: float, limit: int = MAX_OUTPUT_BYTES, on_spawn=None) -> tuple[int, str, str]:
    """Drain both pipes and feed stdin concurrently with a hard memory bound."""
    if os.name != "posix":
        raise RunnerError("process_group_isolation_unavailable")
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    deadline = time.monotonic() + timeout_s
    pending = memoryview(prompt.encode("utf-8"))
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    previous_term_handler = None
    install_term_handler = threading.current_thread() is threading.main_thread()

    def terminate(_signum, _frame):
        raise RunnerError("cancelled")

    if install_term_handler:
        previous_term_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        if on_spawn is not None:
            try:
                on_spawn(proc.pid)
            except Exception:
                # No prompt has been written yet. A missing durable process
                # receipt must abort and kill the owned group, not run untracked.
                raise RunnerError("process_registration_failed") from None
        with selectors.DefaultSelector() as selector:
            for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if pending:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                proc.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RunnerError("timeout")
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == "stdin":
                        try:
                            count = os.write(key.fd, pending[:65536])
                            pending = pending[count:]
                        except BrokenPipeError:
                            pending = memoryview(b"")
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise RunnerError("output_limit_exceeded")
                    buffers[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunnerError("timeout")
            try:
                code = proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise RunnerError("timeout") from exc
        try:
            return (code, buffers["stdout"].decode("utf-8"),
                    buffers["stderr"].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise RunnerError("invalid_utf8_output") from exc
    finally:
        try:
            _kill_group(proc)
        finally:
            if install_term_handler:
                signal.signal(signal.SIGTERM, previous_term_handler)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream and not stream.closed:
                stream.close()


def _strict_json(value: str):
    def pairs(items):
        out = {}
        for key, item in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = item
        return out

    def constant(_):
        raise ValueError("nonfinite number")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def _usage(value) -> dict:
    # Audit metadata is untrusted too: retain numeric counters only, never raw
    # error text or arbitrary fields that might contain a credential or prompt.
    if not isinstance(value, dict):
        return {}
    return {key: v for key, v in value.items() if key in {
        "input_tokens", "output_tokens", "cached_input_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    } and type(v) in (int, float) and math.isfinite(v) and v >= 0}


def _parse_claude(stdout: str) -> tuple[dict, dict]:
    try:
        result = _strict_json(stdout)
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ValueError("missing result wrapper")
        if result.get("is_error") is not False or result.get("subtype") != "success":
            raise RunnerError("provider_reported_failure")
        if result.get("permission_denials"):
            raise RunnerError("tool_permission_denied")
        payload = result.get("structured_output")
        if payload is None and isinstance(result.get("result"), str):
            payload = _strict_json(result["result"])
        if not isinstance(payload, dict):
            raise ValueError("missing JSON object")
        return payload, _usage(result.get("usage"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise RunnerError("malformed_provider_output") from exc


def _parse_codex(stdout: str) -> tuple[dict, dict]:
    """Strict JSONL final-message parser; never treats a failed turn as success.

    Kept separate from execution capability: parsing support does not grant an
    unsafe installed CLI permission to execute.
    """
    try:
        events = [_strict_json(line) for line in stdout.splitlines() if line.strip()]
        payload = None
        usage = {}
        completed = False
        started = False
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("event must be object")
            kind = event.get("type")
            if completed:
                raise ValueError("events after terminal completion")
            if kind not in {"thread.started", "turn.started", "item.started", "item.updated",
                            "item.completed", "turn.completed", "turn.failed", "error"}:
                raise RunnerError("unknown_provider_event")
            if kind == "turn.started":
                if started:
                    raise ValueError("multiple turns")
                started = True
            if kind in {"error", "turn.failed"}:
                raise RunnerError("provider_reported_failure")
            if kind in {"item.started", "item.updated", "item.completed"}:
                item = event.get("item")
                if not isinstance(item, dict):
                    raise ValueError("missing item")
                # 0.153.1 emits this expected startup notice as an error item
                # when code_mode_host=false. It confirms our enforced denial;
                # it is not a failed turn or a model tool call. Only this exact
                # prefix before turn.started is acceptable. Never waive errors
                # produced during inference or unknown startup failures.
                if (not started and kind == "item.completed" and item.get("type") == "error"
                        and isinstance(item.get("message"), str)
                        and item["message"].startswith(CODEX_DISABLED_HOST_NOTICE)):
                    continue
                if item.get("type") not in {"agent_message", "reasoning"}:
                    raise RunnerError("unexpected_tool_event")
                if kind == "item.completed" and item.get("type") == "agent_message":
                    # An earlier commentary message need not be JSON. Only the
                    # final completed agent message is the contract payload.
                    payload = item.get("text")
            if kind == "turn.completed":
                if completed:
                    raise ValueError("multiple completed turns")
                completed = True
                usage = _usage(event.get("usage"))
        if not started or not completed or not isinstance(payload, str):
            raise ValueError("missing final turn")
        result = _strict_json(payload)
        if not isinstance(result, dict):
            raise ValueError("final must be JSON object")
        return result, usage
    except (ValueError, TypeError, RecursionError) as exc:
        raise RunnerError("malformed_provider_output") from exc


def _model_audit(provider: str, stdout: str, requested: str | None) -> dict:
    """Record CLI metadata, never infer the model from its generated answer.

    Claude's result modelUsage can include more than one model; it does not
    identify which one authored the final answer in that case. Codex versions
    that omit model IDs from lifecycle events remain explicitly unreported.
    Aliases and possible snapshot expansions are not treated as exact matches.
    The legacy ``model`` receipt field continues to mean requested model.
    """
    prefix = "claude-" if provider == "claude" else "gpt-"

    def model_id(value):
        return (isinstance(value, str) and value.startswith(prefix)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,119}(?:\[1m\])?", value)
                is not None)

    if provider == "claude":
        metadata = _strict_json(stdout).get("modelUsage")
        reported = [name for name, usage in metadata.items()
                    if model_id(name) and isinstance(usage, dict)] if isinstance(metadata, dict) else []
    else:
        reported = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = _strict_json(line)
            if event.get("type") in {"thread.started", "turn.started", "turn.completed"}:
                value = event.get("model")
                if model_id(value):
                    reported.append(value)
    models = sorted(set(reported))
    actual = models[0] if len(models) == 1 else None
    verification = "unreported"
    if len(models) > 1:
        verification = "multiple_reported"
    elif actual is not None:
        verification = "reported"
        if actual == requested:
            verification = "exact_match"
        elif model_id(requested):
            # A dated snapshot, context-window alias or longer vendor model
            # name may expand an undated request. Retain the observed ID but
            # do not invent equivalence or reject this ambiguous relationship.
            left, right = requested.removesuffix("[1m]"), actual.removesuffix("[1m]")
            related = left == right or left.startswith(right + "-") or right.startswith(left + "-")
            if not related:
                verification = "mismatch"
    return {"reported_model": actual, "reported_models": models,
            "model_verification": verification}


def _claude_argv(binary: str, schema: dict, model: str | None,
                 effort: str | None = None) -> list[str]:
    argv = [binary, "--print", "--safe-mode", "--tools", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--setting-sources", "",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--no-chrome", "--output-format", "json", "--json-schema",
            json.dumps(_claude_schema(schema), ensure_ascii=False, allow_nan=False)]
    if model:
        argv.extend(["--model", model])
    if effort is not None:
        argv.extend(["--effort", effort])
    return argv


def _claude_schema(schema: dict) -> dict:
    """Project the wire schema; the runtime still enforces the full contract.

    CLI 2.1.252's local AJV rejects the  meta-schema declaration. Claude
    constrained output also does not support string bounds or maxItems. Keep
    those constraints as descriptions for generation, never mutate the caller's
    schema, and leave validation of the original contract with the controller.
    See platform.claude.com/docs/en/build-with-claude/structured-outputs.
    """
    passthrough = {"type", "required", "additionalProperties", "enum", "const",
                   "title", "description", "format", "pattern", "default"}
    deferred = {"minLength", "maxLength", "maxItems", "minimum", "maximum",
                "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "uniqueItems"}
    out, bounds = {}, {}
    for key, value in schema.items():
        if key == "$schema":
            continue
        if key in deferred or (key == "minItems" and value not in (0, 1)):
            bounds[key] = value
        elif key == "properties":
            out[key] = {name: _claude_schema(child) for name, child in value.items()}
        elif key == "items":
            out[key] = _claude_schema(value)
        elif key in {"anyOf", "allOf"}:
            out[key] = [_claude_schema(child) for child in value]
        elif key in passthrough or key == "minItems":
            out[key] = json.loads(json.dumps(value, allow_nan=False))
        else:
            raise RunnerError("claude_schema_keyword_unsupported")
    if bounds:
        note = "Host-validated constraints: " + json.dumps(bounds, sort_keys=True, allow_nan=False)
        out["description"] = (out.get("description", "") + "\n" + note).strip()
    return out


def run(provider: str, prompt: str, output_schema: dict, *, model: str | None = None,
        effort: str | None = None,
        timeout_s: float = 1800, cwd: str | Path | None = None, on_spawn=None) -> dict:
    """Run one isolated inference, or fail closed before any model request.

    ``cwd`` is a runtime-owned temporary parent. An empty child directory is
    always created, so caller files and task instructions are not auto-loaded.
    Status is success/failed/timeout/unavailable. Errors never echo CLI stderr.
    """
    started = time.monotonic()
    out = {"status": "failed", "payload": None, "provider": provider,
           "model": model, "usage": {}, "elapsed_s": 0.0, "error": None,
           "runner_version": None, "requested_model": model,
           "requested_effort": effort, "reported_model": None,
           "reported_models": [], "model_verification": "unreported"}
    try:
        if provider not in {"claude", "codex"}:
            raise RunnerError("unsupported_provider")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RunnerError("invalid_prompt")
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise RunnerError("prompt_limit_exceeded")
        if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
            raise RunnerError("invalid_output_schema")
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise RunnerError("invalid_timeout")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise RunnerError("invalid_model")
        if effort is not None and (not isinstance(effort, str)
                                   or effort not in SUPPORTED_EFFORTS[provider]):
            raise RunnerError("invalid_effort")
        if on_spawn is not None and not callable(on_spawn):
            raise RunnerError("invalid_process_callback")
        binary = _binary(provider)
        if not binary:
            out["status"] = "unavailable"
            raise RunnerError("runner_not_found")
        env = _environment()
        # Register every owned process group, including local capability probes.
        # Keep the default call shape stable for injected offline runners.
        def capture(*args):
            return _capture(*args, **({"on_spawn": on_spawn} if on_spawn else {}))

        with tempfile.TemporaryDirectory(prefix="agent-inference-", dir=cwd) as temp:
            isolated = Path(temp)
            code, help_text, _ = capture([binary, "--help"], "", isolated, env,
                                          PROBE_TIMEOUT_S)
            if code:
                out["status"] = "unavailable"
                raise RunnerError("capability_probe_failed")
            code, version, _ = capture([binary, "--version"], "", isolated, env,
                                        PROBE_TIMEOUT_S, 4096)
            if code or not version.strip():
                out["status"] = "unavailable"
                raise RunnerError("version_probe_failed")
            # CLI version format, not arbitrary output, belongs in audit data.
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?\b", version)
            if not match:
                out["status"] = "unavailable"
                raise RunnerError("unknown_runner_version")
            out["runner_version"] = match.group(0)
            if provider == "codex":
                code, exec_help, _ = capture([binary, "exec", "--help"], "", isolated,
                                               env, PROBE_TIMEOUT_S)
                if code or any(flag not in exec_help for flag in CODEX_REQUIRED_FLAGS):
                    out["status"] = "unavailable"
                    raise RunnerError("codex_isolation_flags_unsupported")
                available_flags = help_text + "\n" + exec_help
                if model is not None and "--model" not in available_flags:
                    out["status"] = "unavailable"
                    raise RunnerError("model_flag_unsupported")
                if effort is not None and "--config" not in available_flags:
                    out["status"] = "unavailable"
                    raise RunnerError("effort_flag_unsupported")
                code, stdout, _ = _run_codex(binary, prompt, output_schema, model,
                                              isolated, env, timeout_s,
                                              **({"on_spawn": on_spawn} if on_spawn else {}),
                                              **({"effort": effort} if effort is not None else {}))
                parse = _parse_codex
            else:
                if any(flag not in help_text for flag in CLAUDE_REQUIRED_FLAGS):
                    out["status"] = "unavailable"
                    raise RunnerError("tool_free_isolation_unsupported")
                if model is not None and "--model" not in help_text:
                    out["status"] = "unavailable"
                    raise RunnerError("model_flag_unsupported")
                if effort is not None and "--effort" not in help_text:
                    out["status"] = "unavailable"
                    raise RunnerError("effort_flag_unsupported")
                argv = _claude_argv(binary, output_schema, model, effort)
                code, stdout, _ = capture(argv, prompt, isolated, env, timeout_s)
                parse = _parse_claude
            if code:
                raise RunnerError(f"provider_exit_{code}")
            payload, usage = parse(stdout)
            out.update(_model_audit(provider, stdout, model))
            out["usage"] = usage
            if out["model_verification"] == "mismatch":
                raise RunnerError("model_mismatch")
            out.update(status="success", payload=payload, usage=usage)
    except RunnerError as exc:
        out["error"] = str(exc)
        if str(exc) == "timeout":
            out["status"] = "timeout"
        elif str(exc).endswith("_unavailable"):
            out["status"] = "unavailable"
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        out["error"] = "runner_setup_or_io_failure"
    finally:
        out["elapsed_s"] = round(time.monotonic() - started, 3)
    return out
