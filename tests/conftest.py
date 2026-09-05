"""Make the public test suite isolated from user state and machine paths.

测试目录镜像 quant/ 的分层 (tests/core, tests/decision, ...) —— 找某模块的测试
就去同名子目录。
"""
import ast
import importlib.util
import os
import signal
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 任何测试都不许碰 Telegram 网络 ─────────────────────────────────────────
# 单个测试记得桩发送器不是机制: 这里在唯一的 HTTP 出口上兜底, 没桩到的发送一律
# AssertionError, 测试红, 而不是真的发出去。
import pytest


def pytest_addoption(parser):
    parser.addoption("--live-data", action="store_true", default=False,
                     help="Run audits that read the private trading dataset.")
    parser.addoption("--host-state", action="store_true", default=False,
                     help="Run audits that read installed tasks on this host.")


def pytest_configure(config):
    config.addinivalue_line("markers", "live_data: reads private trading files; requires --live-data")
    config.addinivalue_line("markers", "host_state: reads installed host configuration; requires --host-state")
    # Several entrypoints import core.env while pytest collects test modules.
    # An autouse fixture runs too late to prevent that implicit credential read.
    from quant.core import paths

    patch = pytest.MonkeyPatch()
    patch.setattr(paths, "ENV_PATH", Path(os.devnull))
    config.add_cleanup(patch.undo)
    # moomoo 10.09.6908 opens its file logger at import time and exposes no
    # log-directory setting. Redirect only its exact SDK path construction;
    # leave HOME and every other filesystem path unchanged.
    if "moomoo.common.ft_logger" in sys.modules:
        raise pytest.UsageError("moomoo was imported before test log isolation")
    sdk_logs = tempfile.TemporaryDirectory(prefix="coreytrading-pytest-sdk-")
    original_join = os.path.join
    sdk_prefixes = {(os.environ.get("HOME"), ".com.moomoo.OpenD/Log"),
                    (os.environ.get("appdata"), "com.moomoo.OpenD/Log")}

    def isolated_sdk_join(*parts):
        if len(parts) == 2 and parts in sdk_prefixes:
            return sdk_logs.name
        return original_join(*parts)

    patch.setattr(os.path, "join", isolated_sdk_join)

    def cleanup_sdk_logs():
        module = sys.modules.get("moomoo.common.ft_logger")
        sdk_logger = getattr(module, "logger", None)
        if sdk_logger is not None:
            sdk_logger.file_logger.removeHandler(sdk_logger.fileHandler)
            sdk_logger.fileHandler.close()
        sdk_logs.cleanup()

    config.add_cleanup(cleanup_sdk_logs)
    # Keep the actual SDK types and OpenAPI helpers, but omit the two explicit
    # import-time environment checks. Unit tests must inject their broker calls;
    # no synthetic "OpenD is reachable" cache is written for other processes.
    common_path = ROOT / ".claude/skills/openapi/scripts/common.py"
    if common_path.is_file() and importlib.util.find_spec("moomoo") is not None:
        if "common" in sys.modules:
            raise pytest.UsageError("OpenAPI common was imported before test isolation")
        tree = ast.parse(common_path.read_text(encoding="utf-8"), filename=str(common_path))
        omitted = {"_auto_load_dotenv": 0, "ensure_futu_api": 0}
        body = []
        for node in tree.body:
            call = node.value if isinstance(node, ast.Expr) else None
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in omitted and not call.args and not call.keywords):
                omitted[call.func.id] += 1
            else:
                body.append(node)
        if any(count != 1 for count in omitted.values()):
            raise pytest.UsageError("OpenAPI import checks changed; review test isolation")
        tree.body = body
        spec = importlib.util.spec_from_file_location("common", common_path)
        module = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, "common", module)
        previous_sigint = signal.getsignal(signal.SIGINT)
        try:
            exec(compile(tree, str(common_path), "exec"), module.__dict__)
        finally:
            # The SDK installs a SIGINT handler as another import side effect.
            signal.signal(signal.SIGINT, previous_sigint)
        module.STAMP_FILE = str(Path(sdk_logs.name) / "version-stamp")
        module._ENV_CHECK_CACHE_FILE = str(Path(sdk_logs.name) / "env-check")
        module._CRYPTO_FIRM_CACHE_FILE = str(Path(sdk_logs.name) / "crypto-firm")
        module._detect_ai_type_support()


def pytest_collection_modifyitems(config, items):
    for marker, option in (("live_data", "--live-data"), ("host_state", "--host-state")):
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=f"requires explicit {option}")
        for item in items:
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    from quant.ops import tg_notify

    def _blocked(token, method, body, timeout=10):
        raise AssertionError(
            f"test tried to reach Telegram API ({method}) — stub tg_notify.send_message / "
            f"send_rich_message (or inject send=/send_rich=) instead")

    monkeypatch.setattr(tg_notify, "_post_method", _blocked)
