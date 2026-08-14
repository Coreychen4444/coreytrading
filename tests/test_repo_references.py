"""仓库内部自引用不许烂 —— 文档/注释里写的路径，必须真的存在。

 重组当天，这一类烂了三处而且**全是静默的**:
  · tasks/overnight-briefing.md → `tests/test_pipeline.py`（已移到 tests/ops/）
  · entry_guard.py 注释 → `tests/test_entry_guard.py`
  · level_map.py 注释 → `tests/test_technical.py`
其中第一处进了**定时任务的 live 副本**，也就是说 briefing 每天都在引用一个不存在的文件。
路径改名不报错、不崩溃，只是慢慢地把文档变成假的。

外加一条 live 定时任务的对账 —— `tasks/` 是真源，`~/.claude/scheduled-tasks/` 是任务系统
实际读取的副本，两者脱钩过一次，跑了 2 个月旧版没人发现（见 tasks/README.md）。
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE_TASKS = Path(os.path.expanduser("~/.claude/scheduled-tasks"))
CHECK_LIVE_TASKS = os.environ.get("COREYTRADING_CHECK_LIVE_TASKS") == "1"

# 三个跨切面守卫本身**大量引用路径当例子**（"移动前是 tests/test_pipeline.py"、
# "放一个 quant/decision/options/foo.py 进来"），那些故意指向不存在的东西。
# 它们谈论路径，不使用路径 —— 排除掉，否则这条守卫只会自己咬自己。
SELF = {"tests/test_repo_references.py", "tests/test_layering.py", "tests/test_module_coverage.py"}

# 只扫我们自己维护的文本；.claude/skills/openapi/ 是上游同步来的，不归这里管。
# 根 README 与 tests/ 下的说明文档也是公开入口，必须进入同一条路径引用守卫。
# quant/research 下的设计稿允许记录“某路径不存在”这类负向裁决，暂不整体收编。
SCAN = [
    *(ROOT / "quant").rglob("*.py"),
    *(ROOT / "tests").rglob("*.py"),
    *(ROOT / "tests").rglob("*.md"),
    *(ROOT / "tasks").glob("*.md"),
    *(ROOT / ".claude" / "rules").glob("*.md"),
    *ROOT.glob("README*.md"),
    ROOT / "quant" / "README.md",
    ROOT / "CLAUDE.md",
]

# 指向仓库内代码/测试的 token。只收后缀明确的，避免把散文里的目录名当路径。
# `scripts` 留在里面是**故意的**——  重组后它整个不存在了，所以任何残留的
# `scripts/xxx.py` 都该红。开头的负向 lookbehind 保证 `.claude/skills/openapi/scripts/...`
# （前面是 `/`）匹配不上，那个是上游的目录，还在。
REF = re.compile(r"(?<![\w./-])((?:quant|tests|tasks|scripts)/[\w./-]+\.(?:py|sh|md|tsv))")


def _refs(path: Path):
    for line in path.read_text().splitlines():
        # `git show <sha>:scripts/...` 指的是历史提交里的坐标, 工作树里本来就不该存在
        if "git show" in line or "git log" in line:
            continue
        for m in REF.finditer(line):
            tok = m.group(1)
            if "<" in tok or ">" in tok or "*" in tok:      # 占位符/glob
                continue
            yield tok


def test_every_internal_path_reference_resolves():
    """注释、docstring、任务 prompt 里写的 quant// tests// tasks/ 路径都必须存在。"""
    broken = []
    for f in SCAN:
        if not f.exists() or str(f.relative_to(ROOT)) in SELF:
            continue
        for tok in _refs(f):
            if not (ROOT / tok).exists():
                broken.append(f"{f.relative_to(ROOT)} → {tok}")
    assert not broken, (
        "这些引用指向不存在的文件（移动/改名后没跟着改）:\n  " + "\n  ".join(sorted(set(broken))))


# ── live 定时任务对账 ──────────────────────────────────────────────

# 全量 = tasks/*.md（README 除外）。 由 3 个补到 6 个 —— 此前
# gate-ladder-report / postclose-transition / chart-read 三个新任务不在名单里，
# 于是 chart-read 的 live 副本比 repo 少一整节没人发现（同一天还发现它压根没注册）。
TASKS = {p.stem for p in (ROOT / "tasks").glob("*.md") if p.stem != "README"}


@pytest.mark.skipif(not CHECK_LIVE_TASKS or not LIVE_TASKS.exists(),
                    reason="set COREYTRADING_CHECK_LIVE_TASKS=1 to compare local task copies")
@pytest.mark.parametrize("name", sorted(TASKS))
def test_live_scheduled_task_matches_repo(name):
    """`tasks/` 是真源，live 副本必须逐字节一致。

    脱钩不会报错 —— 定时任务照跑，只是跑的是旧版。 前就这样跑了 2 个月。
    改了 repo 记得: cp tasks/<name>.md ~/.claude/scheduled-tasks/<name>/SKILL.md
    """
    repo = ROOT / "tasks" / f"{name}.md"
    live = LIVE_TASKS / name / "SKILL.md"
    assert repo.exists(), f"tasks/{name}.md 不存在"
    assert live.exists(), f"live 副本缺失: {live}"
    # 末尾换行不算脱钩：app UI 里保存一次任务会把 SKILL.md 的结尾换行去掉
    # （chart-read 实证），那是保存动作的副产品不是内容漂移。
    assert live.read_text().rstrip("\n") == repo.read_text().rstrip("\n"), (
        f"live 副本与 repo 脱钩了 —— 定时任务正在跑旧版。同步:\n"
        f"    cp tasks/{name}.md {live}")


@pytest.mark.skipif(not CHECK_LIVE_TASKS or not LIVE_TASKS.exists(),
                    reason="set COREYTRADING_CHECK_LIVE_TASKS=1 to inspect local task copies")
def test_live_tasks_reference_only_existing_scripts():
    """live 副本里的每个脚本路径都要能跑到 —— 这是监控变黑最直接的失败模式。"""
    broken = []
    for name in sorted(TASKS):
        live = LIVE_TASKS / name / "SKILL.md"
        if not live.exists():
            continue
        for tok in _refs(live):
            if not (ROOT / tok).exists():
                broken.append(f"{name}: {tok}")
    assert not broken, (
        "定时任务引用了不存在的路径（下次触发就是无声失败）:\n  " + "\n  ".join(broken))
