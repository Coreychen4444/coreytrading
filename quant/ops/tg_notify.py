#!/usr/bin/env python3
"""Send a message through the Telegram Bot API.

Usage:
    python3 quant/ops/tg_notify.py "message text"
    echo "message" | python3 quant/ops/tg_notify.py --stdin
    python3 quant/ops/tg_notify.py --test  # send a test ping
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

# Optional credential files, tried in order, then environment variables.  The
# first entry is agent-neutral: any runner (scheduled task, cron, another
# assistant) can own it.  The second is the legacy per-channel location kept for
# compatibility.  Credentials never live in the repository.
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


def _post(token: str, text: str, parse_mode: str | None) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": load_chat_id(), "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


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


def main():
    parser = argparse.ArgumentParser(description="Send Telegram notification")
    parser.add_argument("message", nargs="?", help="Message text")
    parser.add_argument("--stdin", action="store_true", help="Read message from stdin")
    parser.add_argument("--test", action="store_true", help="Send test ping")
    parser.add_argument("--split", action="store_true",
                        help="超 4096 时按段落边界拆成多条依次发（默认超限=整条被拒）")
    parser.add_argument("--plain", action="store_true",
                        help="不走 Markdown 解析 —— 正文里的 _ / * 原样保留")
    args = parser.parse_args()

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

    mode = None if args.plain else "Markdown"
    parts = split_message(text) if args.split else [text]
    for i, part in enumerate(parts, 1):
        result = send_message(part, parse_mode=mode)
        if not result.get("ok"):
            print(f"Failed on part {i}/{len(parts)}: {result}", file=sys.stderr)
            sys.exit(1)
    print("Sent to configured chat" + (f" ({len(parts)} parts)" if len(parts) > 1 else ""))


if __name__ == "__main__":
    main()
