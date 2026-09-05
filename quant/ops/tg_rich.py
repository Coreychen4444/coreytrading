"""tg_rich.py — 报告文本 → Telegram **Rich Message** blocks（Bot API 10.1+）。

「想换平台」的念头核实后是我们出口的天花板不是 Telegram 的：`tg_notify.py` 还在用 2018 年的 `sendMessage` + legacy Markdown（粗体/斜体/代码/链接
四样，4096 上限硬拆）。Telegram 10.1 的 `sendRichMessage` 有原生表格 / h1–h6 / 折叠块 / 分割线 /
脚注，单条 32,768 字符、500 块、20 列（官方 Rich Message Limits，逐字见 core.telegram.org/bots/api）。

**为什么自己编译成 blocks，而不是把 Markdown 交给 Telegram 解析**（`InputRichMessage.markdown`
/ `.html` 字段一律不用）：
  · 雷达/估值正文满是 `$1,137 vs $233B` —— Rich Markdown 里 `$…$` 是行内 LaTeX 公式；
  · `mixed_transition` / `z2_breach_buy_freeze` 这类标识符里的 `_` 在 GFM 里可能被吃成斜体；
  · 本仓 legacy 报告子集 `*粗体*` = 粗体，Rich Markdown 里 `*x*` = 斜体，同一份文本两种意思。
Telegram 永远只收到 blocks JSON：**我们认得的语法才变成结构，其余逐字原样**。可离线测试、
零转义歧义，失败模式只有一种（整条 400）且 tg_notify 会回落 legacy 通道，不丢字。

认得的语法（GFM 子集，行为单位）：
  #..######  标题（size = 级数 + HEADING_OFFSET，夹在 1..6）
  | a | b |  表格（第二行 |---|:-:|--:| 为对齐行时首行 = 表头；>20 列回落 pre 块不丢字）
  ```lang    围栏代码 → pre 块
  ---        分割线（--- / *** / ___ / ━━━ 独占一行）
  - x / * x / + x / • x / 1. x / - [ ] x   列表（缩进 ≥2 空格 = 嵌套；[ ]/[x] = 任务项）
  > x        引用块
  <details [open]><summary>标题</summary> … </details>   折叠块（可嵌套；summary 可另起一行）
  其余连续非空行 = 一个段落（行内 "\n" 原样保留）
行内：**粗体** / *粗体*（legacy 子集语义，不是斜体）/ `代码` / [文字](URL)；`_` 永不解析。

零交易语义：纯函数，不读不写任何 state，不知道自己在为谁渲染。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from quant.core import motto as _motto

Block = Dict[str, Any]
RichText = Union[str, List[Any], Dict[str, Any]]

# ── Telegram 官方上限（Rich Message Limits）与本模块的安全预算 ─────────────────
RICH_CHAR_LIMIT = 32768      # "Up to 32768 UTF-8 characters in the rich message text"
RICH_BLOCK_LIMIT = 500       # "Up to 500 blocks, including nested blocks, list items, table rows, …"
RICH_TABLE_COLS = 20         # "Up to 20 columns in a table"
RICH_NEST_LIMIT = 16         # "Up to 16 levels of nested formatting and blocks"
RICH_CHAR_BUDGET = 30000     # 留 ~8% 余量：官方按 UTF-8 字符数, 我们按码点数, 边界别赌
RICH_BLOCK_BUDGET = 480
HEADING_OFFSET = 1           # `#` → size 2：手机上 size 1 太大, 报告标题从 2 起
DETAILS_MAX_DEPTH = 8        # 折叠块递归深度上限 (官方 16 层含行内格式, 留一半给内容)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_DIVIDER_RE = re.compile(r"^\s*(?:(?:[-*_]\s*){3,}|━{3,})\s*$")
_FENCE_RE = re.compile(r"^\s*```([\w+-]*)\s*$")
_FENCE_END_RE = re.compile(r"^\s*```\s*$")
_LIST_RE = re.compile(r"^(\s*)(?:([-*+•])|(\d{1,3})[.)])\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_DETAILS_OPEN_RE = re.compile(
    r"^\s*<details(?P<open>\s+open)?\s*>\s*(?:<summary>(?P<summary>.*?)</summary>)?\s*(?P<rest>.*)$", re.I)
_DETAILS_CLOSE_RE = re.compile(r"^\s*</details>\s*$", re.I)
_SUMMARY_RE = re.compile(r"^\s*<summary>(?P<summary>.*?)</summary>\s*(?P<rest>.*)$", re.I)
_ALIGN_CELL_RE = re.compile(r"^:?-+:?$")


# ── 行内 ─────────────────────────────────────────────────────────────────────

def _find_link_end(s: str, start: int) -> int:
    """`[label](url)` 的 URL 允许成对括号（与 tg_notify.markdown_subset_to_html 同规）。"""
    depth = 0
    j = start
    while j < len(s) and s[j] not in "\n":
        c = s[j]
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return j
            depth -= 1
        elif c.isspace():
            return -1
        j += 1
    return -1


def parse_inline(s: str, *, _in_bold: bool = False) -> RichText:
    """行内子集 → RichText。只认 `code` / [label](url) / **bold** / *bold*；其余逐字。
    单个 `*`、`_`、`~`、`$` 一律字面量 —— 报告里它们是价格/标识符不是标记。"""
    out: List[Any] = []
    buf: List[str] = []

    def flush():
        if buf:
            out.append("".join(buf))
            buf.clear()

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "`":
            end = s.find("`", i + 1)
            nl = s.find("\n", i + 1)
            if end > i + 1 and (nl == -1 or end < nl):
                flush()
                out.append({"type": "code", "text": s[i + 1:end]})
                i = end + 1
                continue
        elif c == "[":
            label_end = s.find("](", i + 1)
            nl = s.find("\n", i + 1)
            if label_end > i and (nl == -1 or label_end < nl):
                url_end = _find_link_end(s, label_end + 2)
                if url_end > label_end + 2:
                    flush()
                    label = s[i + 1:label_end] or s[label_end + 2:url_end]
                    out.append({"type": "url", "text": parse_inline(label, _in_bold=_in_bold),
                                "url": s[label_end + 2:url_end]})
                    i = url_end + 1
                    continue
        elif c == "*" and not _in_bold:
            if s.startswith("**", i):
                end = s.find("**", i + 2)
                nl = s.find("\n", i + 2)
                if end > i + 2 and (nl == -1 or end < nl):
                    flush()
                    out.append({"type": "bold", "text": parse_inline(s[i + 2:end], _in_bold=True)})
                    i = end + 2
                    continue
            else:
                end = s.find("*", i + 1)
                nl = s.find("\n", i + 1)
                if end > i + 1 and (nl == -1 or end < nl) and not s[i + 1].isspace() \
                        and not s[end - 1].isspace():
                    flush()
                    out.append({"type": "bold", "text": parse_inline(s[i + 1:end], _in_bold=True)})
                    i = end + 1
                    continue
        buf.append(c)
        i += 1
    flush()
    if not out:
        return ""
    if len(out) == 1 and isinstance(out[0], str):
        return out[0]
    return out


def rich_text_plain(rt: RichText) -> str:
    """RichText → 纯文本（计长度 / 找座右铭 / legacy 回落用）。"""
    if rt is None:
        return ""
    if isinstance(rt, str):
        return rt
    if isinstance(rt, list):
        return "".join(rich_text_plain(x) for x in rt)
    return rich_text_plain(rt.get("text", ""))


def rich_text_subset(rt: RichText) -> str:
    """RichText → 本仓 legacy Markdown 子集（`*粗体*` + `[文字](URL)`），给 tg_notify 回落通道
    经 `markdown_subset_to_html` 再发 —— 回落时粗体与链接仍然在，只丢结构。"""
    if rt is None:
        return ""
    if isinstance(rt, str):
        return rt
    if isinstance(rt, list):
        return "".join(rich_text_subset(x) for x in rt)
    t = rt.get("type")
    inner = rich_text_subset(rt.get("text", ""))
    if t == "bold":
        return f"*{inner}*" if inner and "\n" not in inner else inner
    if t == "url":
        return f"[{inner}]({rt.get('url', '')})"
    return inner


# ── 块构造 ───────────────────────────────────────────────────────────────────

def paragraph(text: str) -> Block:
    return {"type": "paragraph", "text": parse_inline(text)}


def heading(text: str, size: int) -> Block:
    return {"type": "heading", "text": parse_inline(text), "size": max(1, min(6, int(size)))}


def footer_block(text: str) -> Block:
    return {"type": "footer", "text": parse_inline(text)}


def divider() -> Block:
    return {"type": "divider"}


def _cell(text: str, *, header: bool = False, align: str = "left") -> Dict[str, Any]:
    cell: Dict[str, Any] = {"text": parse_inline(text), "align": align, "valign": "top"}
    if header:
        cell["is_header"] = True          # 类型 "True": 只在真时出现, 不传 False
    return cell


def _split_row(line: str) -> List[str]:
    """`| a | b \\| c |` → ["a", "b | c"]。首尾的 | 是装饰, 中间 `\\|` 是字面竖线。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, buf, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(s[i])
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _align_of(spec: str) -> str:
    spec = spec.strip()
    if spec.startswith(":") and spec.endswith(":"):
        return "center"
    if spec.endswith(":"):
        return "right"
    return "left"


def table_block(lines: Sequence[str]) -> Block:
    """GFM 管道表 → table 块。>20 列（官方上限）→ pre 块原样兜底, 一个字不丢。"""
    rows = [_split_row(ln) for ln in lines]
    has_header = len(rows) >= 2 and all(_ALIGN_CELL_RE.match(c) for c in rows[1] if c) \
        and any(rows[1])
    aligns = [_align_of(c) for c in rows[1]] if has_header else []
    body = rows[2:] if has_header else rows
    ncols = max(len(r) for r in rows)
    if ncols > RICH_TABLE_COLS:
        return {"type": "pre", "text": "\n".join(lines)}
    cells: List[List[Dict[str, Any]]] = []

    def _row(r: List[str], header: bool) -> List[Dict[str, Any]]:
        r = list(r) + [""] * (ncols - len(r))
        return [_cell(c, header=header, align=(aligns[i] if i < len(aligns) else "left"))
                for i, c in enumerate(r)]

    if has_header:
        cells.append(_row(rows[0], True))
    for r in body:
        cells.append(_row(r, False))
    return {"type": "table", "cells": cells, "is_bordered": True, "is_compact": True}


def _list_blocks(items: List[Tuple[int, Optional[str], Optional[bool], str]]) -> List[Block]:
    """items = [(indent, ordinal or None, checked or None, text)] → 一个或多个嵌套 list 块。
    Telegram: 「list must be either ordered or unordered」—— 同一层里有序/无序切换就另起一个
    list 块（ 首发实测 400）。缩进更深的项挂到上一项的 blocks 里（递归同规）。"""
    def build(start: int, base_indent: int) -> Tuple[List[Block], int]:
        blocks_out: List[Block] = []
        cur: List[Dict[str, Any]] = []
        cur_ordered: Optional[bool] = None
        i = start
        while i < len(items):
            indent, ordinal, checked, text = items[i]
            if indent < base_indent:
                break
            if indent > base_indent and cur:
                children, i = build(i, indent)
                cur[-1]["blocks"].extend(children)
                continue
            if indent > base_indent:                       # 首项就缩进: 当作本层
                indent = base_indent
            ordered = ordinal is not None
            if cur and ordered != cur_ordered:
                blocks_out.append({"type": "list", "items": cur})
                cur = []
            cur_ordered = ordered
            item: Dict[str, Any] = {"blocks": [paragraph(text)]}
            if ordered:
                item["type"] = "1"
                item["value"] = int(ordinal)
            if checked is not None:
                item["has_checkbox"] = True
                if checked:
                    item["is_checked"] = True
            cur.append(item)
            i += 1
        if cur:
            blocks_out.append({"type": "list", "items": cur})
        return blocks_out, i

    if not items:
        return []
    out, _ = build(0, items[0][0])
    return out


def _details_block(summary: str, inner_lines: List[str], is_open: bool, depth: int) -> Block:
    blk: Block = {"type": "details", "summary": parse_inline(summary or "详情"),
                  "blocks": compile_blocks("\n".join(inner_lines), _depth=depth + 1) or [paragraph("")]}
    if is_open:
        blk["is_open"] = True
    return blk


# ── 编译 ─────────────────────────────────────────────────────────────────────

def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and "|" in s[1:]


def compile_blocks(text: str, *, heading_offset: int = HEADING_OFFSET, _depth: int = 0) -> List[Block]:
    """报告文本 → InputRichBlock 列表。认得的语法变结构, 其余逐字进段落。"""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    blocks: List[Block] = []
    para: List[str] = []

    def flush_para():
        if para:
            blocks.append(paragraph("\n".join(para)))
            para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        m = _FENCE_RE.match(line)
        if m:
            flush_para()
            lang = m.group(1)
            j = i + 1
            body: List[str] = []
            while j < n and not _FENCE_END_RE.match(lines[j]):
                body.append(lines[j])
                j += 1
            blk: Block = {"type": "pre", "text": "\n".join(body)}
            if lang:
                blk["language"] = lang
            blocks.append(blk)
            i = j + 1
            continue
        m = _DETAILS_OPEN_RE.match(line)
        if m and _depth < DETAILS_MAX_DEPTH:
            flush_para()
            is_open = bool(m.group("open"))
            summary = m.group("summary")
            rest = m.group("rest") or ""
            j = i + 1
            inner: List[str] = []
            if summary is None:
                # summary 可以另起一行: <details>\n<summary>…</summary>
                if j < n:
                    sm = _SUMMARY_RE.match(lines[j])
                    if sm:
                        summary, rest2 = sm.group("summary"), sm.group("rest") or ""
                        j += 1
                        if rest2.strip() and not _DETAILS_CLOSE_RE.match(rest2):
                            inner.append(rest2)
            if rest.strip():
                if _DETAILS_CLOSE_RE.match(rest):
                    blocks.append(_details_block(summary or "", inner, is_open, _depth))
                    i = j
                    continue
                inner.append(rest)
            level = 1
            while j < n:
                if _DETAILS_OPEN_RE.match(lines[j]):
                    level += 1
                elif _DETAILS_CLOSE_RE.match(lines[j]):
                    level -= 1
                    if level == 0:
                        break
                inner.append(lines[j])
                j += 1
            blocks.append(_details_block(summary or "", inner, is_open, _depth))
            i = j + 1
            continue
        m = _HEADING_RE.match(line)
        if m:
            flush_para()
            blocks.append(heading(m.group(2), len(m.group(1)) + heading_offset))
            i += 1
            continue
        if _DIVIDER_RE.match(line):
            flush_para()
            blocks.append(divider())
            i += 1
            continue
        if _is_table_line(line):
            flush_para()
            j = i
            tl: List[str] = []
            while j < n and _is_table_line(lines[j]):
                tl.append(lines[j])
                j += 1
            blocks.append(table_block(tl))
            i = j
            continue
        m = _LIST_RE.match(line)
        if m:
            flush_para()
            items: List[Tuple[int, Optional[str], Optional[bool], str]] = []
            j = i
            while j < n:
                lm = _LIST_RE.match(lines[j])
                if not lm:
                    break
                indent = len(lm.group(1).replace("\t", "  "))
                ordinal = lm.group(3)
                body_text = lm.group(4)
                checked: Optional[bool] = None
                tm = _TASK_RE.match(body_text)
                if tm and ordinal is None:
                    checked = tm.group(1).lower() == "x"
                    body_text = tm.group(2)
                items.append((indent, ordinal, checked, body_text))
                j += 1
            blocks.extend(_list_blocks(items))
            i = j
            continue
        m = _QUOTE_RE.match(line)
        if m:
            flush_para()
            j = i
            ql: List[str] = []
            while j < n and _QUOTE_RE.match(lines[j]):
                ql.append(_QUOTE_RE.match(lines[j]).group(1))
                j += 1
            inner_blocks = compile_blocks("\n".join(ql), _depth=_depth + 1) or [paragraph("")]
            blocks.append({"type": "blockquote", "blocks": inner_blocks})
            i = j
            continue
        para.append(line)
        i += 1
    flush_para()
    return blocks


# ── 度量 / 拆分 ──────────────────────────────────────────────────────────────

def block_text(block: Block) -> str:
    """一个块（含嵌套）的全部纯文本。"""
    t = block.get("type")
    if t == "table":
        return "\n".join(" | ".join(rich_text_plain(c.get("text", "")) for c in row)
                         for row in block.get("cells", []))
    if t == "list":
        return "\n".join(blocks_plain(it.get("blocks", [])) for it in block.get("items", []))
    if t in ("blockquote",):
        return blocks_plain(block.get("blocks", []))
    if t == "details":
        return rich_text_plain(block.get("summary", "")) + "\n" + blocks_plain(block.get("blocks", []))
    return rich_text_plain(block.get("text", ""))


def blocks_plain(blocks: Sequence[Block]) -> str:
    return "\n".join(block_text(b) for b in blocks)


def count_blocks(blocks: Sequence[Block]) -> int:
    """按官方口径数块：嵌套块、列表项、表格行都算。"""
    n = 0
    for b in blocks:
        n += 1
        t = b.get("type")
        if t == "table":
            n += len(b.get("cells", []))
        elif t == "list":
            for it in b.get("items", []):
                n += 1 + count_blocks(it.get("blocks", []))
        elif t in ("blockquote", "details"):
            n += count_blocks(b.get("blocks", []))
    return n


def char_len(blocks: Sequence[Block]) -> int:
    return sum(len(block_text(b)) for b in blocks)


def _shrink(block: Block, max_chars: int, max_blocks: int) -> List[Block]:
    """单个块就超预算 → 切成几个同类块, 不丢字。表格重复表头; 段落/pre 按行; 列表按项。"""
    t = block.get("type")
    if t == "table":
        cells = block.get("cells", [])
        header = cells[0] if cells and cells[0] and cells[0][0].get("is_header") else None
        body = cells[1:] if header else cells
        out: List[Block] = []
        chunk: List[Any] = []
        for row in body:
            cand = ([header] if header else []) + chunk + [row]
            trial = dict(block, cells=cand)
            if chunk and (char_len([trial]) > max_chars or count_blocks([trial]) > max_blocks):
                out.append(dict(block, cells=([header] if header else []) + chunk))
                chunk = [row]
            else:
                chunk.append(row)
        if chunk:
            out.append(dict(block, cells=([header] if header else []) + chunk))
        return out or [block]
    if t == "list":
        items = block.get("items", [])
        out, chunk = [], []
        for it in items:
            trial = dict(block, items=chunk + [it])
            if chunk and (char_len([trial]) > max_chars or count_blocks([trial]) > max_blocks):
                out.append(dict(block, items=chunk))
                chunk = [it]
            else:
                chunk.append(it)
        if chunk:
            out.append(dict(block, items=chunk))
        return out or [block]
    if t in ("paragraph", "pre"):
        text = rich_text_plain(block.get("text", ""))
        out, buf = [], ""
        for ln in text.split("\n"):
            cand = f"{buf}\n{ln}" if buf else ln
            if buf and len(cand) > max_chars:
                out.append({**block, "text": parse_inline(buf) if t == "paragraph" else buf})
                buf = ln
            else:
                buf = cand
            while len(buf) > max_chars:                       # 单行还超 → 硬切
                out.append({**block, "text": parse_inline(buf[:max_chars]) if t == "paragraph" else buf[:max_chars]})
                buf = buf[max_chars:]
        if buf:
            out.append({**block, "text": parse_inline(buf) if t == "paragraph" else buf})
        return out or [block]
    return [block]


def split_blocks(blocks: Sequence[Block], *, max_chars: Optional[int] = None,
                 max_blocks: Optional[int] = None) -> List[List[Block]]:
    """按官方上限拆成多条 rich message。**超限的后果是整条 400 拒收**（与 4096 时代同一种
    静默变黑），所以拆条是代码不是 prose。边界优先落在块之间；一条不以标题收尾
    （标题挪到下一条开头, 免得标题和它的正文分家）。"""
    max_chars = RICH_CHAR_BUDGET if max_chars is None else max_chars
    max_blocks = RICH_BLOCK_BUDGET if max_blocks is None else max_blocks
    parts: List[List[Block]] = []
    cur: List[Block] = []
    cur_chars = cur_blocks = 0
    queue = list(blocks)
    while queue:
        b = queue.pop(0)
        bc, bb = char_len([b]), count_blocks([b])
        if bc > max_chars or bb > max_blocks:
            pieces = _shrink(b, max_chars, max_blocks)
            if len(pieces) > 1 or pieces[0] is not b:
                queue = pieces + queue
                continue
        if cur and (cur_chars + bc > max_chars or cur_blocks + bb > max_blocks):
            carry: List[Block] = []
            if cur[-1].get("type") == "heading" and len(cur) > 1:
                carry = [cur.pop()]
            parts.append(cur)
            cur, cur_chars, cur_blocks = [], 0, 0
            queue = carry + [b] + queue
            continue
        cur.append(b)
        cur_chars += bc
        cur_blocks += bb
    if cur:
        parts.append(cur)
    return parts or [[]]


# ── 可选尾行（与 tg_notify.append_motto 同合同, 块版；未配置 = 原样返回） ────────

def has_motto(parts: Sequence[Sequence[Block]]) -> bool:
    return _motto.configured() and any(_motto.MOTTO in blocks_plain(p) for p in parts)


def append_motto(parts: List[List[Block]], *, max_chars: Optional[int] = None,
                 max_blocks: Optional[int] = None) -> List[List[Block]]:
    """可选尾行 = footer 块, 落最后一条末尾; 装不下单独一条; 正文已有不重复; 未配置原样返回。"""
    if not _motto.configured():
        return list(parts)
    max_chars = RICH_CHAR_BUDGET if max_chars is None else max_chars
    max_blocks = RICH_BLOCK_BUDGET if max_blocks is None else max_blocks
    if not parts or parts == [[]]:
        return [[footer_block(_motto.MOTTO_LINE)]]
    if has_motto(parts):
        return parts
    fb = footer_block(_motto.MOTTO_LINE)
    last = parts[-1]
    if char_len(last) + char_len([fb]) <= max_chars and count_blocks(last) + 1 <= max_blocks:
        return parts[:-1] + [list(last) + [fb]]
    return list(parts) + [[fb]]


# ── 回落：blocks → legacy 子集文本 ───────────────────────────────────────────

def _cell_text(c: Dict[str, Any]) -> str:
    return rich_text_subset(c.get("text", ""))


def blocks_to_text(blocks: Sequence[Block], *, indent: str = "", sep: str = "\n\n") -> str:
    """blocks → 本仓 legacy Markdown 子集文本（`*粗体*` / `[文字](URL)`）。rich 通道被拒时
    tg_notify 用它走 4096 拆条 + HTML 转换的老路 —— 结构降级为文本, 一个字不丢。
    块之间空一行（= 老 split_message 的拆条边界）, 表格行/列表项之间单换行。"""
    out: List[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            out.append(f"{indent}*{rich_text_plain(b.get('text', ''))}*")
        elif t in ("paragraph", "footer"):
            out.append(indent + rich_text_subset(b.get("text", "")).replace("\n", "\n" + indent))
        elif t == "pre":
            out.append(indent + rich_text_plain(b.get("text", "")).replace("\n", "\n" + indent))
        elif t == "divider":
            out.append(f"{indent}———")
        elif t == "table":
            out.append("\n".join(indent + " | ".join(_cell_text(c) for c in row)
                                 for row in b.get("cells", [])))
        elif t == "list":
            rows: List[str] = []
            for k, it in enumerate(b.get("items", []), 1):
                mark = f"{it.get('value', k)}." if it.get("type") else "•"
                if it.get("has_checkbox"):
                    mark += " [x]" if it.get("is_checked") else " [ ]"
                inner = blocks_to_text(it.get("blocks", []), indent=indent + "  ", sep="\n")
                first, _, rest = inner.partition("\n")
                rows.append(f"{indent}{mark} {first.strip()}" + (f"\n{rest}" if rest else ""))
            out.append("\n".join(rows))
        elif t == "blockquote":
            inner = blocks_to_text(b.get("blocks", []), indent=indent)
            out.append("\n".join(f"{indent}> {ln.strip()}" for ln in inner.split("\n")))
        elif t == "details":
            out.append(f"{indent}▸ *{rich_text_plain(b.get('summary', ''))}*\n"
                       + blocks_to_text(b.get("blocks", []), indent=indent + "  "))
        else:
            txt = rich_text_subset(b.get("text", ""))
            if txt:
                out.append(indent + txt)
    return sep.join(x for x in out if x != "")


def to_payload(blocks: Sequence[Block]) -> Dict[str, Any]:
    """`sendRichMessage` 的 rich_message 字段。自动实体识别一律关掉：报告里的 `/reports`、
    `@98.7`、`$MU` 不该变成命令/提及/cashtag（显式 [文字](URL) 链接不受影响）。"""
    return {"blocks": copy.deepcopy(list(blocks)), "skip_entity_detection": True}
