#!/usr/bin/env python3
"""Send a message through the Telegram Bot API.

Usage:
    python3 quant/ops/tg_notify.py "message text"
    echo "message" | python3 quant/ops/tg_notify.py --stdin
    python3 quant/ops/tg_notify.py --test  # send a test ping
    cat report.md | python3 quant/ops/tg_notify.py --stdin --rich   # 富消息 (标题/表格/折叠块)

两条通道 (**rich 是默认**, 告警/一行通知/自动建卡通知一律同一条通道):
  · 默认 = rich: Bot API 10.1 `sendRichMessage` —— `tg_rich.compile_blocks` 把本仓的 GFM 子集
    (# 标题 / | 表格 | / <details> / 列表 / **粗体** / [链接]) 编译成 blocks JSON, Telegram 永远
    不解析 Markdown (价格里的 $ 与标识符里的 _ 零歧义); 32,768 字符 / 500 块上限由
    `tg_rich.split_blocks` 代码级拆条; rich 通道被拒 (4xx) → 同一份内容回落 legacy 通道
    (blocks → 子集文本 → HTML) **一个字不丢**, 并在 stderr 说明。`--rich` 保留为无操作兼容旗。
  · `--legacy`: 老路 `sendMessage` + 4096 拆条 (`--split` / `--plain` 只在这条路上有意义) ——
    只给排障/回退用, 定时任务不再直接走它。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import html
import json
import os
import time
import urllib.request
import urllib.error

from quant.core import motto as _motto
from quant.ops import tg_rich

# Codex/Claude-neutral credential location.  Keep the legacy Claude channel path
# as a compatibility fallback for the other, still-Claude-owned trading tasks.
TOKEN_ENV_PATHS = (
    os.path.expanduser("~/.config/coreytrading/telegram.env"),
    os.path.expanduser("~/.claude/channels/telegram/.env"),
)


def _read_env_value(key: str) -> str:
    for env_path in TOKEN_ENV_PATHS:
        if not os.path.exists(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    return ""


def load_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or _read_env_value("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID not found in a credential file or the environment")
    return chat_id


def load_bot_token() -> str:
    token = _read_env_value("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not found in a credential file or the environment")
    return token


TG_LIMIT = 4096      # sendMessage 单条上限，按 **UTF-16 码元**算（不是字符数）


def _tg_len(s: str) -> int:
    """Telegram 数的是 UTF-16 码元 —— emoji 一个算两个。用 len() 会低估。"""
    return len(s.encode("utf-16-le")) // 2


def markdown_subset_to_html(text: str) -> str:
    """Render our tiny report Markdown subset as Telegram-safe HTML.

    The source format intentionally supports only ``*bold*`` and
    ``[label](URL)``.  Everything else is HTML-escaped, so identifiers such as
    ``mixed_transition`` remain literal text instead of accidentally opening a
    Markdown entity.  Link URLs may contain balanced parentheses.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "*":
            end = text.find("*", i + 1)
            newline = text.find("\n", i + 1)
            if end > i + 1 and (newline == -1 or end < newline):
                out.append("<b>" + html.escape(text[i + 1:end]) + "</b>")
                i = end + 1
                continue

        if text[i] == "[":
            label_end = text.find("](", i + 1)
            newline = text.find("\n", i + 1)
            if label_end > i + 1 and (newline == -1 or label_end < newline):
                url_start = label_end + 2
                depth = 0
                url_end = -1
                j = url_start
                while j < len(text) and text[j] != "\n":
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        if depth == 0:
                            url_end = j
                            break
                        depth -= 1
                    j += 1
                if url_end > url_start:
                    label = html.escape(text[i + 1:label_end])
                    url = html.escape(text[url_start:url_end], quote=True)
                    out.append(f'<a href="{url}">{label}</a>')
                    i = url_end + 1
                    continue

        out.append(html.escape(text[i]))
        i += 1
    return "".join(out)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """超长消息 → 多条 ≤limit，**优先在空行处断开**（段落/每票块的边界）。

    超限的后果不是"截断"是**整条 400 拒收**—— 一个字都不会到，属于本仓最熟的
    那种静默变黑。所以拆条这件事必须是代码：靠 prose 指令让人/agent 手工
    "每条 ≤2 只票"，票一多或某只票多两个形态就会悄悄越界。
    """
    if _tg_len(text) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    for block in text.split("\n\n"):
        cand = f"{buf}\n\n{block}" if buf else block
        if _tg_len(cand) <= limit:
            buf = cand
            continue
        if buf:
            out.append(buf)
        if _tg_len(block) <= limit:
            buf = block
            continue
        buf = ""                                  # 单块就超限 → 退一级按行拆
        for line in block.split("\n"):
            cand = f"{buf}\n{line}" if buf else line
            if _tg_len(cand) <= limit:
                buf = cand
                continue
            if buf:
                out.append(buf)
            buf = ""
            while _tg_len(line) > limit:          # 单行还超 → 硬切，不丢字
                cut = limit
                while _tg_len(line[:cut]) > limit:
                    cut -= 1
                out.append(line[:cut])
                line = line[cut:]
            buf = line
    if buf:
        out.append(buf)
    return out


def append_motto(parts: list[str], limit: int = TG_LIMIT) -> list[str]:
    """可选尾行收尾 (operator 通过 TG_FOOTER_LINE 配置; 未配置 = 原样返回)。

    落在**最后一条**的末尾; 装不下就单独成一条 —— 绝不让一条原本合法的消息因为多了
    这一行而被 400 拒收。正文里已有 (agent 手写了 / entry_guard 表头带了) 则不重复。
    """
    if not _motto.configured():
        return parts
    if not parts:
        return [_motto.MOTTO_LINE]
    if any(_motto.has_motto(p) for p in parts):
        return parts
    tail = _motto.with_motto(parts[-1])
    if _tg_len(tail) <= limit:
        return parts[:-1] + [tail]
    return parts + [_motto.MOTTO_LINE]


RICH_PART_GAP_S = 1.0    # 多条 rich message 之间的间隔, 避开每聊天限速


def _post_method(token: str, method: str, body: dict, timeout: int = 10) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post(token: str, text: str, parse_mode: str | None) -> dict:
    body = {"chat_id": CHAT_ID, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    return _post_method(token, "sendMessage", body)


def _post_rich(token: str, blocks: list[dict]) -> dict:
    body = {"chat_id": CHAT_ID, "rich_message": tg_rich.to_payload(blocks)}
    return _post_method(token, "sendRichMessage", body, timeout=20)


def send_rich_message(blocks: list[dict], retries: int = 3) -> dict:
    """一条 rich message (blocks 已在官方上限内, 拆条归 tg_rich.split_blocks)。
    5xx / 网络错误退避重试; 4xx (内容被拒 / token 错) 立即抛 —— 调用方决定要不要回落 legacy。"""
    token = load_bot_token()
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _post_rich(token, blocks)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                raise RuntimeError(f"Telegram API error {e.code} (no retry): {body}")
            last_err = RuntimeError(f"Telegram API error {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
        if attempt < retries:
            wait = 2 * attempt
            print(f"[tg_notify] rich attempt {attempt} failed ({last_err}); retrying in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Telegram rich send failed after {retries} attempts: {last_err}")


def rich_parts(text: str, *, motto: bool = True) -> list[list[dict]]:
    """文本 → 已按官方上限拆好的 rich message 列表 (可选尾行 = footer 块, 与 append_motto 同合同)。"""
    parts = tg_rich.split_blocks(tg_rich.compile_blocks(text))
    return tg_rich.append_motto(parts) if motto else parts


def send_rich(text: str, *, motto: bool = True, send_part=None, legacy_send=None,
              sleep_fn=time.sleep) -> dict:
    """报告 → rich 通道; 任一条被拒 → 剩余内容回落 legacy (子集文本 → 4096 拆条 → HTML)。
    返回 {"mode": "rich" | "legacy_fallback", "parts": 已发条数, "fallback_from": 出错条序号?}。"""
    send_part = send_part or send_rich_message
    legacy_send = legacy_send or send_message
    parts = rich_parts(text, motto=motto)
    sent = 0
    try:
        for i, part in enumerate(parts):
            if i:
                sleep_fn(RICH_PART_GAP_S)
            resp = send_part(part)
            if isinstance(resp, dict) and not resp.get("ok", True):
                raise RuntimeError(f"Telegram rejected rich part {i + 1}/{len(parts)}: {resp}")
            sent += 1
    except RuntimeError as exc:
        print(f"[tg_notify] rich send failed on part {sent + 1}/{len(parts)}: {exc}; "
              f"falling back to legacy for the remainder", file=sys.stderr)
        rest = [b for part in parts[sent:] for b in part]
        legacy_parts = split_message(tg_rich.blocks_to_text(rest))
        if motto:
            legacy_parts = append_motto(legacy_parts)
        for j, lp in enumerate(legacy_parts):
            if j:
                sleep_fn(RICH_PART_GAP_S)
            legacy_send(markdown_subset_to_html(lp), parse_mode="HTML")
        return {"mode": "legacy_fallback", "parts": sent + len(legacy_parts),
                "rich_parts": sent, "fallback_from": sent + 1, "error": str(exc)}
    return {"mode": "rich", "parts": sent}


def send_message(text: str, parse_mode: str = "Markdown", retries: int = 3) -> dict:
    """Send with retry. Transient failures (network, timeout, 5xx) are retried
    with backoff; a 400 Markdown parse error falls back to plain text once;
    other 4xx fail immediately (bad token/chat — retrying won't help)."""
    token = load_bot_token()
    last_err: Exception | None = None
    mode: str | None = parse_mode
    for attempt in range(1, retries + 1):
        try:
            return _post(token, text, mode)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 400 and "parse" in body.lower() and mode is not None:
                # Entity parse failure — retry immediately as plain text. Keep the
                # API description/offset in stderr so the next failure is diagnosable.
                print(f"[tg_notify] {mode} parse failed: {body}; falling back to plain text",
                      file=sys.stderr)
                mode = None
                continue
            if 400 <= e.code < 500:
                raise RuntimeError(f"Telegram API error {e.code} (no retry): {body}")
            last_err = RuntimeError(f"Telegram API error {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
        if attempt < retries:
            wait = 2 * attempt
            print(f"[tg_notify] attempt {attempt} failed ({last_err}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Telegram send failed after {retries} attempts: {last_err}")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Send Telegram notification")
    parser.add_argument("message", nargs="?", help="Message text")
    parser.add_argument("--stdin", action="store_true", help="Read message from stdin")
    parser.add_argument("--test", action="store_true", help="Send test ping")
    parser.add_argument("--split", action="store_true",
                        help="超 4096 时按段落边界拆成多条依次发（默认超限=整条被拒）")
    parser.add_argument("--plain", action="store_true",
                        help="不走 Markdown 解析 —— 正文里的 _ / * 原样保留")
    parser.add_argument("--no-motto", action="store_true",
                        help="不附加座右铭尾行 (只给零交易语义的消息; 默认每条都带)")
    parser.add_argument("--rich", action="store_true",
                        help="(默认, 兼容旗) 富消息通道 sendRichMessage: # 标题 / | 表格 | / <details> 折叠 / 列表 / "
                             "**粗体** / [链接] 原生渲染; 32k 上限自动拆条; 被拒回落 legacy 不丢字")
    parser.add_argument("--legacy", action="store_true",
                        help="老路 sendMessage + 4096 拆条 (排障/回退用; --split / --plain 只在这条路上有意义)")
    parser.add_argument("--dry-run", action="store_true",
                        help="不发送, 打印将要发的内容 (默认 rich 时打印 blocks JSON)")
    args = parser.parse_args(argv)
    if args.rich and args.legacy:
        parser.error("--rich 与 --legacy 互斥")

    if args.test:
        text = "coreytrading test ping ✓"
    elif args.stdin:
        text = sys.stdin.read().strip()
    elif args.message:
        text = args.message
    else:
        parser.error("Provide a message, --stdin, or --test")

    if not text:
        print("Empty message, skipping", file=sys.stderr)
        sys.exit(0)

    if not args.legacy:
        if args.dry_run:
            print(json.dumps(rich_parts(text, motto=not args.no_motto), ensure_ascii=False, indent=1))
            return
        res = send_rich(text, motto=not args.no_motto)
        tag = "rich" if res["mode"] == "rich" else f"legacy fallback from part {res['fallback_from']}"
        print(f"Sent to chat {CHAT_ID} [{tag}]" + (f" ({res['parts']} parts)" if res["parts"] > 1 else ""))
        return

    mode = None if args.plain else "Markdown"
    parts = split_message(text) if args.split else [text]
    if not args.no_motto:
        parts = append_motto(parts)
    if args.dry_run:
        print("\n\n<<<PART>>>\n\n".join(parts))
        return
    for i, part in enumerate(parts, 1):
        result = send_message(part, parse_mode=mode)
        if not result.get("ok"):
            print(f"Failed on part {i}/{len(parts)}: {result}", file=sys.stderr)
            sys.exit(1)
    print("Sent to configured chat" + (f" ({len(parts)} parts)" if len(parts) > 1 else ""))


if __name__ == "__main__":
    main()
