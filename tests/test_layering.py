"""分层不变量 —— 让 quant/README.md 的规则变成会失败的东西。

这个仓库反复栽在同一处: **规则不进代码 = 不存在**（sizing 的整股档位、卡片
constraints、fired_cards 播报，全是「文档里写着、代码里没有」）。 把
扁平的 scripts/ 重组成分层包，如果分层只写在 README 里，三个月后它就退化回扁平——
只是多了几个目录名。

所以这里钉四件事:
  1. 依赖方向单向向下（模块级零例外；函数内 lazy 向上必须报备理由）
  2. 目录深度锁死两级（bootstrap 的 parents[2] 是按这个算的）
  3. bootstrap 只有一种写法（不许再各写各的 sys.path）
  4. 新层必须排进依赖序（不能凭空多出一个没人知道该放哪的目录）
"""
import ast
from pathlib import Path

import pytest

QUANT = Path(__file__).resolve().parents[1] / "quant"

# 依赖序 —— 与 quant/README.md 的层次图同源。数字小 = 底层。
# 同 rank = 同层，互相 import 允许（例: data/kline_cache → data/market_data）。
LAYERS = {
    "core": 0,        # 纯共享库，谁都能用，它谁都不用
    "data": 1,        # 外部世界 → 本地
    "sync": 2,        # broker 真值 ↔ state
    "options": 3,     # 期权合约级参数
    "risk": 3,        # 仓位 / 集中度
    "decision": 4,    # 机械闸门
    "journal": 5,     # 账本写入
    "report": 5,      # 读时报告
    "ops": 6,         # 编排 / 通知
    "research": 7,    # 验证层旁路：可以看任何层，谁也不该看它
}

# 函数内 lazy 的向上 import。模块级一律不许，这里也只收「import 该模块作为库时
# 根本不会执行到」的那种。key = (相对 quant/ 的路径, 被 import 的模块)。
LAZY_UPWARD_ALLOWED = {
    ("core/technical.py", "quant.data.market_data"):
        "CLI-only。在 main() 里 argparse 之后才 import —— `import quant.core.technical` "
        "作为库时永远不执行，包级依赖图仍然是干净的 core→∅。CLI 用法有文档引用 "
        "(bottom-confirmation-signals.md / technical-indicators-framework.md)，不能删。",
}

BOOTSTRAP = "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))"


def _modules():
    return [p for p in sorted(QUANT.rglob("*.py")) if p.name != "__init__.py"]


def _quant_imports(path: Path):
    """[(被 import 的 quant 模块, 行号, 是否模块级)]"""
    tree = ast.parse(path.read_text())
    top_level = {id(n) for n in tree.body}
    found = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            if name.startswith("quant."):
                found.append((name, node.lineno, id(node) in top_level))
    return found


def _layer_of(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) >= 2 and parts[1] in LAYERS else None


def _upward(path: Path):
    """这个文件里所有「向上」的 import。"""
    src = LAYERS[path.relative_to(QUANT).parts[0]]
    out = []
    for module, lineno, is_top in _quant_imports(path):
        dst_layer = _layer_of(module)
        if dst_layer is not None and LAYERS[dst_layer] > src:
            out.append((module, lineno, is_top))
    return out


# ── 1. 依赖方向 ────────────────────────────────────────────────────

def test_no_module_level_upward_import():
    """模块级向上 import = 真实的包级倒挂依赖。零例外。

    这条一旦破，`core` 就不再是「谁都能安全 import 的底」，循环 import 只是时间问题。
    """
    bad = []
    for p in _modules():
        rel = p.relative_to(QUANT)
        for module, lineno, is_top in _upward(p):
            if is_top:
                bad.append(f"{rel}:{lineno} — {rel.parts[0]} 层 import 了 {module}")
    assert not bad, (
        "依赖只能向下 (quant/README.md)。把共用逻辑下沉到更低的层，"
        "或者重新想想这个模块是不是放错层了:\n  " + "\n  ".join(bad))


def test_lazy_upward_imports_are_declared():
    """函数内 lazy 的向上 import 允许存在，但必须在 LAZY_UPWARD_ALLOWED 里写明理由。

    不然「把 import 缩进到函数里」就成了绕开分层的万能后门。
    """
    undeclared = []
    for p in _modules():
        rel = str(p.relative_to(QUANT))
        for module, lineno, is_top in _upward(p):
            if not is_top and (rel, module) not in LAZY_UPWARD_ALLOWED:
                undeclared.append(f"{rel}:{lineno} → {module}")
    assert not undeclared, (
        "向上的 lazy import 必须报备 —— 在 tests/test_layering.py 的 "
        "LAZY_UPWARD_ALLOWED 里加一条并写清为什么 import 该模块作为库时不会执行到它:\n  "
        + "\n  ".join(undeclared))


def test_lazy_upward_allowlist_has_no_stale_entries():
    """豁免名单只能因为真实存在的 import 而存在。代码改了名单没跟着改 = 名单在腐烂。"""
    live = {(str(p.relative_to(QUANT)), module)
            for p in _modules() for module, _, is_top in _upward(p) if not is_top}
    stale = sorted(set(LAZY_UPWARD_ALLOWED) - live)
    assert not stale, (
        "这些豁免已经没有对应的 import 了，从 LAZY_UPWARD_ALLOWED 删掉:\n  "
        + "\n  ".join(f"{f} → {m}" for f, m in stale))


def test_research_is_a_bypass_nobody_depends_on():
    """回测/校准可以看任何层，但没有任何实时路径可以依赖它。

    research 里的东西是一次性的、参数常改的。一旦 decision 或 report 开始 import 它，
    改一次回测就可能动到真金白银的判定。
    """
    bad = []
    for p in _modules():
        if p.relative_to(QUANT).parts[0] == "research":
            continue
        for module, lineno, _ in _quant_imports(p):
            if _layer_of(module) == "research":
                bad.append(f"{p.relative_to(QUANT)}:{lineno} → {module}")
    assert not bad, "实时路径不许依赖 research/:\n  " + "\n  ".join(bad)


# ── 2/3/4. 结构不变量 ──────────────────────────────────────────────

def test_every_layer_is_ranked():
    """quant/ 下新开一个目录，必须同时在 LAYERS 里给它排位。

    没排位的层 = 没人知道它能 import 谁、谁能 import 它，分层当场失效。
    """
    dirs = {d.name for d in QUANT.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))}
    unranked = sorted(dirs - set(LAYERS))
    assert not unranked, (
        f"新层 {unranked} 还没排进依赖序。在 LAYERS 里给个 rank，"
        f"并同步更新 quant/README.md 的层次图。")


def test_package_depth_is_exactly_two():
    """quant/<层>/<模块>.py —— 不许更深。

    bootstrap 写死 parents[2]。放一个 quant/decision/options/foo.py 进来，
    它算出来的 "repo root" 会是 quant/ —— 不报错，只是**默默**指错地方，
    然后 paths.ROOT 下面所有账本路径全歪。
    """
    too_deep = [str(p.relative_to(QUANT)) for p in QUANT.rglob("*.py")
                if len(p.relative_to(QUANT).parts) > 2]
    assert not too_deep, (
        "目录深度锁死两级 (quant/README.md)。要加第三级，先改所有模块的 "
        f"bootstrap parents[2]:\n  {too_deep}")


def test_bootstrap_line_is_uniform():
    """可执行模块的 sys.path bootstrap 只有一种写法。

    重组前每个文件自己 sys.path 找同伴，写法五花八门（parent / parents[1] /
    ROOT/"scripts" / 甚至硬编码 /Users/example/...）。统一成一行才能被 grep、被这条测试守住。
    """
    offenders = []
    for p in _modules():
        rel = p.relative_to(QUANT)
        # core/paths.py 是唯一豁免 —— add_openapi_path() 本身就是这条规则指向的那个机制
        # (openapi skill 的 `common` 不是包，只能靠 sys.path 够到)。下面单独钉它的规模。
        if str(rel) == "core/paths.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            s = line.strip()
            if s.startswith("sys.path.insert") and not s.startswith(BOOTSTRAP):
                offenders.append(f"{rel}:{i}: {s}")
    assert not offenders, (
        f"bootstrap 只允许这一行:\n    {BOOTSTRAP}\n"
        "需要 openapi skill 的 `common` 就用 quant.core.paths.add_openapi_path()。\n  "
        + "\n  ".join(offenders))


def test_only_add_openapi_path_touches_sys_path_in_core_paths():
    """core/paths.py 的豁免不许扩张: 有且只有 add_openapi_path() 里那一处。"""
    tree = ast.parse((QUANT / "core" / "paths.py").read_text())
    inserts = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "insert" and ast.unparse(n.func.value) == "sys.path"]
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "add_openapi_path"), None)
    assert fn is not None, "core/paths.py 必须提供 add_openapi_path()"
    assert len(inserts) == 1, f"core/paths.py 只该有 1 处 sys.path.insert, 现在有 {len(inserts)}"
    assert fn.lineno < inserts[0] <= (fn.end_lineno or inserts[0]), \
        "唯一那处 sys.path.insert 必须在 add_openapi_path() 函数体内"


def test_core_uses_relative_imports_internally():
    """core/ 内部互相引用用相对 import —— 这一层要能自洽，不该知道自己叫 quant.core。"""
    bad = []
    for p in sorted((QUANT / "core").glob("*.py")):
        for module, lineno, _ in _quant_imports(p):
            if module.startswith("quant.core"):
                bad.append(f"core/{p.name}:{lineno} → {module}  (应写成 from .{module.split('.')[-1]} import ...)")
    assert not bad, "core 内部请用相对 import:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_layer_has_a_docstring(layer):
    """每层的 __init__.py 要说清自己装什么 —— 这是「新脚本放哪」的第一落点。"""
    init = QUANT / layer / "__init__.py"
    assert init.exists(), f"quant/{layer}/__init__.py 缺失"
    doc = ast.get_docstring(ast.parse(init.read_text()))
    assert doc and len(doc) > 40, f"quant/{layer}/__init__.py 需要一段说明它装什么的 docstring"
