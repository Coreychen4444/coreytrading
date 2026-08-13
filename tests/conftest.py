"""Make the public test suite isolated from user state and machine paths.

测试目录镜像 quant/ 的分层 (tests/core, tests/decision, ...) —— 找某模块的测试
就去同名子目录。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The moomoo SDK creates a log file at import time under HOME. Point that
# unavoidable vendor side effect at a disposable location before any test
# imports broker-facing modules. This also prevents tests from seeing a user's
# real OpenD files or Claude task copies through HOME-relative paths.
TEST_HOME = Path(tempfile.gettempdir()) / "tradingops-test-home"
TEST_HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(TEST_HOME)
os.environ["TRADINGOPS_STATE_LOCK_PATH"] = str(TEST_HOME / "state.lock")
