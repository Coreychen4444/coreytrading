#!/usr/bin/env python3
"""Dispatch missing-card research through the model-neutral controller.

The detector remains detached and never waits for model work. Routing controls
provider, independent review and whether reviewed proposals may be applied.
Workers cannot write state or notify; the controller owns the controlled writer.
The job claim is serialized across callers. A force flag skips cooldown only,
never starts a second worker while the first still owns the card job.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

from quant.core.holdings import cardless_tickers, live_tickers  # noqa: E402
from quant.core.io_json import atomic_write_json, read_json, state_lock  # noqa: E402
from quant.core.paths import POSITIONS_PATH, ROOT, TMP_DIR, WATCHLIST_PATH  # noqa: E402
from quant.core.timeutil import et_today
from quant.ops.agent_runtime import load_routes  # noqa: E402

STATE_PATH = TMP_DIR / "card_autopaint.json"
LOG_DIR = TMP_DIR / "card_autopaint"
COOLDOWN_S = 6 * 3600          # = heartbeat 一轮: 失败下轮重试, 不无限刷进程
MAX_SPAWN_PER_RUN = 2          # 一轮最多起几个 (整个自选一夜清空 ≠ 一次起十个 agent)
SPAWN_TIMEOUT_S = 30 * 60      # 子 agent 自己的墙钟上限, 由 timeout(1) 兜底
# Covers the runtime's maximum primary/fallback/review wall time plus bookkeeping.
# Missing launch receipts remain fenced for this window, even under --force.
STARTING_LEASE_S = 4 * SPAWN_TIMEOUT_S + 120


class SpawnNotStartedError(RuntimeError):
    """The launch failed before Popen returned a child process."""


def _display_path(p: Path) -> str:
    """日志路径的可读形式。repo 内 → 相对路径; repo 外 → 原样绝对路径。

    ⚠️ 不用裸 `relative_to(ROOT)`: LOG_DIR 不在 repo 下时它**抛异常**, 而这是
    一行纯展示代码 —— 让"路径怎么印"把整个 spawn 弄失败, 是把装饰性的东西
    接进了功能路径。
    """
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _alive(pid) -> bool:
    """上一轮起的 agent 还在跑吗 (signal 0 = 只探活不发信号)。"""
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def load_state() -> Dict:
    try:
        return read_json(STATE_PATH) or {}
    except FileNotFoundError:
        return {}


def due(targets: List[str], state: Dict, now: float, force: bool = False) -> tuple:
    """→ (要起的, [(ticker, 跳过原因)])。纯函数, 可测。"""
    jobs = state.get("jobs") or {}
    go, skipped = [], []
    for t in targets:
        j = jobs.get(t) or {}
        if _alive(j.get("pid")):
            skipped.append((t, f"上一轮建卡仍在跑 (pid {j.get('pid')}, "
                               f"日志 {j.get('log')})"))
            continue
        if j.get("status") == "starting" and float(j.get("lease_until_ts") or 0) > now:
            skipped.append((t, "启动回执尚未确认，保留 starting lease；force 不绕过该租约"))
            continue
        age = now - float(j.get("spawned_at_ts") or 0)
        if age < COOLDOWN_S and not force:
            skipped.append((t, f"距上次建卡 {age / 3600:.1f}h < 冷却 "
                               f"{COOLDOWN_S / 3600:.0f}h — 下一轮再试"))
            continue
        go.append(t)
    return go, skipped


def build_prompt(ticker: str, bucket: str, today: str) -> str:
    """Compatibility description; policy loading belongs to the role runtime."""
    cmd_bucket = "current" if bucket == "positions" else "watchlist"
    return (f"{today}: 为 {ticker} 提出技术卡片，目标 --bucket {cmd_bucket}。"
            f"只碰 {ticker} 这一张卡；不下单。"
            "zone-maintenance.md、macro-context-check.md、position-tiers.md 由运行器显式加载。"
            "模型只返回提案，update_position_zones.py 由主控在独立复核和版本校验后调用。")


def _timeout_argv(seconds: int) -> List[str]:
    """墙钟兜底的 argv 前缀。**macOS 没有 GNU `timeout(1)`** ——  实证:
    `Popen(["timeout", ...])` 直接 FileNotFoundError, 而日志文件在 Popen 之前
    已创建 → 留下 0 字节日志; 异常被吞进 errors → `spawned` 为空 → state 从不
    落盘 → 冷却永远空转 (heartbeat 与 briefing 各起一次, 连续几轮全部 0 字节, 卡片一张没建成)。一个 argv[0] 解释全部三个症状。

    顺序: GNU timeout / gtimeout (brew coreutils) → perl `alarm+exec` (macOS
    自带; alarm 计时器穿过 exec 存活, SIGALRM 默认动作 = 终止, 等价的墙钟) →
    [] 无兜底 (spawn 照常起, job 标 timeout_enforced=False 报出来, 不静默)。
    """
    for name in ("timeout", "gtimeout"):
        found = shutil.which(name)
        if found:
            return [found, str(seconds)]
    perl = shutil.which("perl") or "/usr/bin/perl"
    if Path(perl).exists():
        return [perl, "-e", "alarm shift; exec @ARGV", str(seconds)]
    return []


def spawn(ticker: str, bucket: str, today: str, route: dict) -> Dict:
    """Launch only our controller, never a model-specific executable."""
    proc = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_DIR / f"{ticker}-{time.strftime('%Y%m%dT%H%M%S')}.log"
        argv = [sys.executable, str(ROOT / "quant/ops/agent_runtime.py"),
                "card", "--ticker", ticker, "--bucket", bucket]
        if route["apply"]:
            argv.append("--apply")
        with log.open("wb") as fh:
            proc = subprocess.Popen(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                    stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    except Exception as exc:
        if proc is None:
            raise SpawnNotStartedError(f"launch not started ({type(exc).__name__})") from exc
        raise  # A child exists; keep the starting fence if later bookkeeping fails.
    return {"ticker": ticker, "bucket": bucket, "pid": proc.pid,
            "log": _display_path(log), "timeout_enforced": True,
            "timeout_owner": "agent_runtime", "mode": "reviewed_apply" if route["apply"] else "shadow",
            "spawned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "spawned_at_ts": time.time()}


def run(dry_run: bool = False, max_spawn: int = MAX_SPAWN_PER_RUN,
        only: Optional[List[str]] = None, force: bool = False) -> Dict:
    if max_spawn < 0:
        raise ValueError("max_spawn must be nonnegative")
    positions, watchlist = read_json(POSITIONS_PATH), read_json(WATCHLIST_PATH)
    targets = cardless_tickers(positions, watchlist)
    if only:
        want = {t.upper() for t in only}
        targets = [t for t in targets if t in want]
    out: Dict = {"cardless": targets, "spawned": [], "skipped": [], "errors": []}
    try:
        policy = load_routes()["autopaint"]
        out["mode"] = "reviewed_apply" if policy["apply"] else "shadow"
        if not policy["enabled"]:
            out["skipped"] = [{"ticker": t, "reason": "autopaint_disabled"} for t in targets]
            return out
        def dispatch():
            state = load_state()
            go, skipped = due(targets, state, time.time(), force=force)
            skipped += [(t, f"本轮上限 {max_spawn} 个 — 下一轮起") for t in go[max_spawn:]]
            go = go[:max_spawn]
            out["skipped"] = [{"ticker": t, "reason": why} for t, why in skipped]
            jobs = state.setdefault("jobs", {})
            live = live_tickers(positions)
            planned = [{"ticker": t, "bucket": "positions" if t in live else "watchlist"} for t in go]
            if dry_run:
                out["dry_run"] = planned
                return
            for target in planned:
                ticker = target["ticker"]
                stamp = time.time()
                starting = {**target, "status": "starting", "launch_id": uuid.uuid4().hex,
                            "spawned_at_ts": stamp, "lease_until_ts": stamp + STARTING_LEASE_S,
                            "mode": "reviewed_apply" if policy["apply"] else "shadow"}
                old_job = jobs.get(ticker)
                jobs[ticker] = starting
                try:
                    # No process may start unless its conservative launch fence
                    # is already durable. This closes the Popen/receipt window.
                    atomic_write_json(STATE_PATH, state)
                except Exception as exc:
                    if old_job is None:
                        jobs.pop(ticker, None)
                    else:
                        jobs[ticker] = old_job
                    out["errors"].append(f"{ticker}: starting lease could not be persisted ({type(exc).__name__}); not launched")
                    continue
                try:
                    job = spawn(ticker, target["bucket"], et_today(), policy)
                except SpawnNotStartedError as exc:
                    jobs[ticker] = {**starting, "status": "failed", "lease_until_ts": 0,
                                    "error": "launch_not_started"}
                    try:
                        atomic_write_json(STATE_PATH, state)
                    except Exception:
                        jobs[ticker] = starting  # The durable starting lease still wins.
                    out["errors"].append(f"{ticker}: dispatch failed before launch ({type(exc).__name__})")
                    continue
                except Exception as exc:
                    # Unknown failure after entering spawn may follow a child
                    # launch; never clear its already-persisted lease.
                    out["errors"].append(f"{ticker}: launch outcome uncertain ({type(exc).__name__}); starting lease retained")
                    continue
                receipt = {**starting, **job, "status": "running", "receipt_persisted": True}
                jobs[ticker] = receipt
                try:
                    atomic_write_json(STATE_PATH, state)
                except Exception as exc:
                    jobs[ticker] = starting
                    receipt = {**receipt, "receipt_persisted": False}
                    out["errors"].append(f"{ticker}: launch receipt persistence failed ({type(exc).__name__}); starting lease retained")
                out["spawned"].append(receipt)
        if dry_run:
            dispatch()
        else:
            with state_lock(STATE_PATH.with_suffix(".lock")):
                dispatch()
    except (ValueError, OSError, KeyError) as exc:
        out["errors"].append(f"agent routing/state unavailable ({type(exc).__name__})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="缺卡标的 → 后台领域研究与受控卡片提案")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只报要起谁, 不起进程")
    ap.add_argument("--max-spawn", type=int, default=MAX_SPAWN_PER_RUN)
    ap.add_argument("--tickers", nargs="*", help="只处理这些 (默认全部缺卡的)")
    ap.add_argument("--force", action="store_true", help="跳过冷却；仍不重复启动在跑的进程")
    a = ap.parse_args()

    r = run(dry_run=a.dry_run, max_spawn=a.max_spawn, only=a.tickers, force=a.force)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
        return 0
    if not r["cardless"]:
        print("card_autopaint: 无缺卡标的")
        return 0
    print(f"缺卡: {', '.join(r['cardless'])}")
    print("模式: " + ("影子研究，仅保存提案与复核" if r.get("mode") == "shadow" else "复核后受控写卡"))
    for j in r.get("dry_run", []):
        print(f"  (dry-run) 会起后台研究: {j['ticker']} [{j['bucket']}]")
    for j in r["spawned"]:
        print(f"  ▶ 后台研究 {j['ticker']} [{j['bucket']}] pid={j['pid']} 日志 {j['log']}")
    for s in r["skipped"]:
        print(f"  · 跳过 {s['ticker']}: {s['reason']}")
    for e in r["errors"]:
        print(f"  ⚠️ {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
