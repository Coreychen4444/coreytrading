# `quant/` — 交易系统代码

 由扁平的 `scripts/`（45 个文件平铺 + 每个文件自己 `sys.path` 找同伴）重组为分层包。
**这份文件是分层的真源**：新脚本放哪、能 import 谁，看这里。

## 分层与依赖方向

依赖只能**向下**。上层可以 import 下层，反之不行；同层之间尽量不互相 import。

```
  ops          编排 / 通知 / 运维
   │           pipeline · tg_notify · commit_state.sh · sync_memory.sh
   ├── report  报告层（读时计算，不产生新状态）
   │           portfolio_brief · perf_report · gate_ladder_report · gate_audit
   │           chart_read（每日收盘判读，**判读层零授权效力**）
   ├── journal 账本写入层（prose-only 不算落库）
   │           annotate_event · decision_log
   ├── decision 决策层（机械闸门）
   │           entry_guard · exit_guard · regime_transition · regime_score
   │           structure_drift · level_map · catalyst_check · tape_check
   │    ├── options  期权执行参数   option_plan · implied_move · strike_compare
   │    └── risk     仓位/集中度     kelly_size · chain_layers
   ├── sync    broker 真值 ↔ state（唯一写 broker block 的层）
   │           state_io · stock_fetch · order_sync · trade_history_sync
   │           watchlist_sync · watchlist_migrate · price_reminder_sync
   │           lot_reconcile（买入落账） · close_reconcile（平仓收尾） · today_group_sync
   ├── data    把外部世界读进本地
   │           market_data · macro_fetch · x_fetch
   │           kline_cache · iv_logger · nav_snapshot · skhy_premium
   └── core    纯共享库 · 单一真源 · 无副作用
               paths · env · io_json · timeutil · num · occ · schema · ledger
               sizing · structure · drift · technical · holdings
               episodes · book · earnings · sectors · patterns（形态判别式，Lo 2000 工序）

  research     验证层（回测/校准）—— 旁路，不参与实时决策
```

## 每层一句话

| 层 | 职责 | 判断标准（新脚本该不该放这） |
|---|---|---|
| `core/` | 被所有层共享的纯函数与路径/IO 真源 | 无副作用、不碰网络、不知道自己在为谁服务 |
| `data/` | 外部世界 → 本地文件 | 会拉外部数据或写 `market/` 下的账本 |
| `sync/` | broker 真值 ↔ `state/*.json` | 会写 `positions.json` / `watchlist.json` |
| `decision/` | 产出机械判定（PLAN/VETO、告警、regime 转移） | 输出是**动作**，不是数据 |
| `options/` | 期权合约级执行参数 | 需要期权链 / Greeks / BS 定价 |
| `risk/` | 单档上限与组合集中度 | 回答"能下多少"而非"要不要下" |
| `journal/` | 决策与成交写进账本 | 往 `events/` / `scores.jsonl` append |
| `report/` | 读时计算的人类可读输出 | 只读不写（除告警冷却状态） |
| `ops/` | 编排、通知、仓库运维 | 调别的脚本，或与外部系统（Telegram/git）打交道 |
| `research/` | 回测与校准 | 只吃历史数据，产出统计而非动作 |

## 约定

**运行**——每个模块既能 import 也能直接跑：

```bash
.venv/moomoo/bin/python3 quant/ops/pipeline.py --mode briefing --json
python3 quant/risk/kelly_size.py --help
```

直接跑靠的是模块顶部这一行 bootstrap（`core/` 里的纯库没有，它们只被 import）：

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

因此**目录深度固定为两级**（`quant/<层>/<模块>.py`）。要加第三级，先改这行。

**import**——一律绝对路径 `from quant.<层>.<模块> import ...`；只有 `core/` 内部互相引用用相对
`from .paths import ...`（这一层是自洽的包，不该知道自己叫 `quant.core`）。

**路径**——推导 repo root 只有一个地方：`core/paths.py`。业务代码里写
`Path(__file__).resolve().parent.parent` 等于把"自己在第几层"编码进逻辑—— 重组时
正是这种写法让 5 个文件（`portfolio_brief` / `stock_fetch` / `trade_history_sync` /
`pipeline` / `_env`）一起指错目录。需要什么路径就从 `paths` 拿，缺就往 `paths` 加。

**OpenD**——`common` 是 openapi skill 的模块、不是包，只能靠 `sys.path`。统一走
`core.paths.add_openapi_path()`，不要再各写各的相对路径。

## 已删除：一次性迁移脚本

原 `scripts/maintenance/` 的 4 个脚本（`enrich_events_20260611` / `migrate_events_tz_20260611` /
`reconcile_positions_20260611` / `migrate_to_state_v2`）**删除**。都已跑过、无任何文档引用，
其中两个早已跑不动（依赖不存在的 `../cockpit` 仓库 / 读已废弃的 `lots["core"]` 层级）。

删除的真正理由是**它们是上了膛的枪**：三个既没有 argparse 也没有 dry-run，任何一次调用
（哪怕 `--help`）都直接重写 append-only 的成交账本或 `positions.json`。 重构时对全部入口
跑 `--help` 冒烟，`enrich_events_20260611` 就这样把 `scores.jsonl` 回滚到了 6-11 的快照，
**删掉 30 条里的 12 条**（6/22 之后全部，含 NOK LEAPS 强平那笔和 7/16 整批退出评分）。已从 git 还原。

需要考古时从重构前那个 commit 取：

```bash
git show b98a42e:scripts/maintenance/enrich_events_20260611.py
git show b98a42e:scripts/migrate_to_state_v2.py
```

**新的一次性迁移脚本**：跑完就删，或者从第一天就带 `--apply`（缺省 dry-run）。不要在
`quant/` 里留一个"只读历史、不要再跑"的目录——那句话拦不住任何东西。

## 测试

`tests/` 镜像同一套分层——找某模块的测试就去同名子目录：

```bash
.venv/moomoo/bin/python3 -m pytest -q             # 全量
.venv/moomoo/bin/python3 -m pytest tests/core -q  # 只跑共享库
.venv/moomoo/bin/python3 -m pytest --collect-only -q | tail -1  # 现查总数
```

**tests/ 为什么不放进 quant/**：测试要横着 import 每一层，放进包里就成了一个「可以任意方向
import」的成员，上面那条依赖规则当场要开例外；而且它是三级深度，破坏 `parents[2]` bootstrap。
包外是 Python 惯例，也让「依赖只能向下」保持零例外。

### 这份文档的规则是被强制的，不是自觉遵守的

上面每一条约定都有对应的失败测试——这个仓库反复栽在「规则只写在文档里」上，所以分层本身也照此办理：

| 守卫 | 钉住什么 |
|---|---|
| `tests/test_layering.py` | 依赖单向向下（模块级零例外，lazy 向上须报备理由且名单不许腐烂）· 深度锁死两级 · bootstrap 唯一写法 · `core/` 内部相对 import · research 是旁路无人依赖 · 新层必须排进依赖序 |
| `tests/test_module_coverage.py` | `quant/<层>/` ↔ `tests/<层>/` 镜像 · 新模块要么有测试要么进 `UNTESTED` 并写明原因 · 补了测试必须从名单删（棘轮，名单只能变短） |
| `tests/test_repo_references.py` | 注释/文档/任务 prompt 里写的 `quant/` `tests/` `scripts/` 路径必须真的存在 · **live 定时任务副本与 `tasks/` 逐字节一致**（脱钩不会报错，只会静静跑旧版） |

两组守卫都做过变异测试：逐条制造违规，确认**每一条都真的会红**（一个只会绿的守卫比没有守卫更坏——
 就有一次冒烟检查因为 macOS 没有 `timeout` 而对 51 个入口全部"通过"）。

`UNTESTED` 的条数不要抄进文档；名单和每条理由都在 `tests/test_module_coverage.py` 中，
需要时现读现算。它是棘轮：补上测试的模块必须出列，名单只能缩短。

仍标记为 **真缺口** 的项目应优先处理，尤其是 `report/perf_report`、
`research/exit_rules_backtest` 与 `research/structure_downgrade_backtest`：
它们会影响绩效解释或参数裁决，而判据来源本身仍需要更完整的机械件验证。
