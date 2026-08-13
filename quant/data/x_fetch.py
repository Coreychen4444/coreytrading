#!/usr/bin/env python3
"""x_fetch.py — 读取 X (Twitter) 公开 post / article 全文,供研报因子卡分析。

用法:
    python3 quant/data/x_fetch.py <url_or_id> [--json]
    python3 quant/data/x_fetch.py https://x.com/GodotSancho/status/2059825788598567296

机制: App-only Bearer Token(读 .env 的 X_BEARER_TOKEN)调 GET /2/tweets。
只读公开内容,不需要 OAuth user context。
正文优先级: article.plain_text(X Article 长文) > note_tweet.text(长推文) > text(标准推文)。

要点(踩过的坑):
- X Article 用承载它的 **Post ID**+ tweet.fields=article 读取,正文在 article.plain_text。
  不是用 article id 去 lookup(那会 not found)。
- article 与 note_tweet 是**分开**的字段,都要显式请求。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error

API = "https://api.x.com/2/tweets"


def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def extract_id(s):
    """从 x.com/twitter.com/.../status/<id>、裸 ID 等提取 Post ID。"""
    m = re.search(r'status(?:es)?/(\d+)', s)
    if m:
        return m.group(1)
    if s.strip().isdigit():
        return s.strip()
    raise SystemExit(f"[x_fetch] 无法从 '{s}' 提取 Post ID(支持 .../status/<id> 或裸 ID)")


def fetch(tid, bearer):
    params = urllib.parse.urlencode({
        'ids': tid,
        'tweet.fields': 'created_at,author_id,note_tweet,article,conversation_id,'
                        'referenced_tweets,public_metrics,lang,entities',
        'expansions': 'author_id,referenced_tweets.id,referenced_tweets.id.author_id',
        'user.fields': 'name,username,verified',
    })
    req = urllib.request.Request(
        API + "?" + params,
        headers={'Authorization': f'Bearer {bearer}'},
)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        if e.code == 401:
            raise SystemExit("[x_fetch] 401 Unauthorized — bearer token 失效或错误,"
                             "去 developer portal 确认/regenerate 后更新 .env 的 X_BEARER_TOKEN")
        if e.code == 429:
            raise SystemExit("[x_fetch] 429 — rate limit,稍后再试")
        raise SystemExit(f"[x_fetch] HTTP {e.code}: {body[:300]}")


def render(data, tid):
    rows = data.get('data', [])
    if not rows:
        errs = data.get('errors', [])
        detail = errs[0].get('detail') if errs else '无 data 返回'
        return f"[x_fetch] 没读到 Post {tid}: {detail}"
    tw = rows[0]
    users = {u['id']: u for u in data.get('includes', {}).get('users', [])}
    author = users.get(tw.get('author_id'), {})
    art = tw.get('article') or {}
    note = tw.get('note_tweet') or {}
    full = art.get('plain_text') or note.get('text') or tw.get('text', '')
    pm = tw.get('public_metrics', {})
    cid = tw.get('conversation_id')
    is_thread = bool(cid and cid != tid)

    if art.get('plain_text'):
        typ = f"X Article — 《{art.get('title', '无标题')}》"
    elif note.get('text'):
        typ = '长推文(note_tweet 全文)'
    else:
        typ = '标准推文(<=280)'
    if is_thread:
        typ += f" | thread(conversation_id {cid})"

    out = []
    out.append(f"作者   : {author.get('name', '?')} (@{author.get('username', '?')})")
    out.append(f"时间   : {tw.get('created_at', '?')}")
    out.append(f"链接   : https://x.com/i/status/{tid}")
    out.append(f"互动   : like {pm.get('like_count', '?')} / RT {pm.get('retweet_count', '?')} "
               f"/ reply {pm.get('reply_count', '?')} / view {pm.get('impression_count', '?')}")
    out.append(f"类型   : {typ}")
    out.append("=" * 64)
    out.append(full)
    refs = data.get('includes', {}).get('tweets', [])
    if refs:
        out.append("=" * 64)
        out.append("引用/回复链中的 tweet:")
        for rt in refs:
            out.append(f"  [{rt['id']}] {(rt.get('text') or '')[:240]}")
    return "\n".join(out)


def main():
    argv = sys.argv[1:]
    as_json = '--json' in argv
    pos = [a for a in argv if not a.startswith('--')]
    if not pos:
        raise SystemExit("用法: python3 quant/data/x_fetch.py <url_or_id> [--json]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = load_env(os.path.join(root, '.env'))
    bearer = env.get('X_BEARER_TOKEN') or os.environ.get('X_BEARER_TOKEN')
    if not bearer:
        raise SystemExit("[x_fetch] 缺 X_BEARER_TOKEN(.env 或环境变量)")
    tid = extract_id(pos[0])
    data = fetch(tid, bearer)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(data, tid))


if __name__ == '__main__':
    main()
