# LOCAL PATCHES — 官方 skill 升级时的移植清单

> 本 skill 基底 = Moomoo 官方 opend-skills 包（`https://openapi.moomoo.com/skills/opend-skills.zip`，
> GitHub `MoomooOpen/moomoo-agent-hub`）。当前基底版本 **v0.1.1**（同步）。
>
> **升级流程**：下载新 zip → 解压对比（重点 common.py 函数面 + 我们 7 个项目脚本 import 的符号）→
> 全量替换 scripts/+docs/ → **照本清单逐项重打补丁**→ 跑下方验证清单 → commit。
> 上次因没有这张清单，官方内容从 4 月冻结到 6 月才同步。

## 必须移植的补丁（丢失即系统瘫痪/降级）

| # | 文件 | 补丁 | 丢失后果 |
|---|------|------|---------|
| P1 | `scripts/common.py` | `_auto_load_dotenv()`（向上搜 .env 注入 env，非覆盖） | 所有脚本读不到 FUTU_* 配置 |
| P2 | `scripts/common.py` | RSA 全套：FutuConfig `opend_encrypt/opend_rsa_key` 字段 + get_config 读 env + `_apply_rsa_config()` + `_encrypt_kwargs()` + 三个 context 工厂接线 | **全部脚本连不上 OpenD**（本机强制加密；官方至 v0.1.1 无 RSA） |
| P3 | `scripts/common.py` | `_redirect_sdk_logging_to_stderr()`（SDK stdout logger → stderr） | `--json` 输出被 SDK 日志污染，不可管道 |
| P4 | `scripts/common.py` | 错误 print → `file=sys.stderr`（_check_opend_alive / _check_opend_reachable / ensure_futu_api ImportError / check_ret 非 json 分支） | 同上 |
| P5 | `scripts/common.py` | `OrderBookType` try/except 垫片（10.7-only 符号，官方硬 import = 上游 bug） | SDK 10.6 下**所有**脚本 import 即炸 |
| P6 | `scripts/trade/get_portfolio.py` + `get_all_portfolios.py` | `show_option_strategy_view` kwarg 仅在显式请求时传（10.7-only） | 10.6 下查持仓直接 TypeError |
| P7 | `scripts/quote/get_snapshot.py` | 期权字段（option_valid/strike/IV/Δ/Γ/θ/V/ρ/OI/expiry_distance）+ 文本模式 [Option] 行 | iv 快查/Greeks 巡检降级 |
| P8 | `SKILL.md` | frontmatter `name: openapi`（官方叫 moomooapi，目录与全 repo 引用都是 openapi）+ "本工作区适配" 段 | skill 调用名漂移 + 工作区约定丢失 |
| P10 | `scripts/trade/get_portfolio.py` + `get_all_portfolios.py` | `acc_id` 过 `to_jsonable()`（SDK 10.9 起 `position_list_query` 的 `acc_id` 是 **numpy int64**，10.6 时不是） | `--json` 直接返回 `{"error": "Object of type int64 is not JSON serializable"}` —— **持仓查询整个不可用**，且报错长得像业务错误不像类型错误 |
| P9 | `scripts/quote/get_snapshot.py` | 盘前/盘后字段（`pre_price/pre_change_rate/pre_volume` + `after_*` 同三件）+ `_ext_float`（NaN/缺数据 → None 非 0）+ 文本模式 [Pre-Mkt]/[After-Hrs] 行 | 盘后方向只能靠 WebSearch 转述（GOOGL AH -3.9% 被外媒措辞误读"微跌"源案例） |

## 已验证由官方自带（不需移植）
- place_order 安全栏（REAL `--confirmed` 硬约束 + MASTER 账户拦截 + `~/.futu_trade_audit.jsonl` 审计）
- 不再 auto-pip（缺 SDK 提示 `/install-moomoo-opend`）
- 版本戳/env 检查缓存机制（`~/.moomoo_skill_version` 写 SKILL_VERSION 即可消 WARN）

## SDK 版本矩阵
- 官方 skill 声称最低 SDK **10.4.6408**（ai_type）；crypto 需 ≥10.5.6508
- 实际：6 个脚本硬依赖 **10.7.6708**符号（ComboLeg / OptionStrategyLeg / OptionStrategyType / StrategyLegAction / OrderBookType）→ 见 SKILL.md 适配段第 5 条。**升到 10.9.6908 后这些符号已全部存在**，P5/P6 垫片变成无害兼容层（不要删——降级回 10.6 时还得靠它们）
- 升级 SDK 必须同步升级 OpenD（big-version 匹配铁律，tools-environment.md）

## 升级后验证清单（全绿才算完成）

⚠️ **不要用 `2>&1` 跑这些命令**—— SDK 的 connect 日志走 stderr（P3 就是干这个的），合并后会
看起来像 stdout 被污染，制造假警报（踩过）。要验 P3 就 `2>/dev/null` 后喂 `json.load`。

标的用**当前实际持仓**，不要照抄下面的例子（NOK/MU 早已清仓，用 qty=0 的票验证不了 trade ctx）。

```bash
.venv/moomoo/bin/python3 .claude/skills/openapi/scripts/quote/get_snapshot.py US.INTC US.INTC270115C120000 --json 2>/dev/null   # RSA + Greeks + P9 盘前后
.venv/moomoo/bin/python3 .claude/skills/openapi/scripts/trade/get_portfolio.py --currency USD --json 2>/dev/null                # trade ctx + 货币 + P6/P10
.venv/moomoo/bin/python3 quant/sync/trade_history_sync.py --days 3 --dry-run --json 2>/dev/null                                 # 项目脚本兼容
.venv/moomoo/bin/python3 -c "import sys; sys.path.insert(0,'.'); from quant.data.market_data import get_kline; print(len(get_kline('INTC', num=3)))"
.venv/moomoo/bin/python3 quant/sync/order_sync.py
.venv/moomoo/bin/python3 -m pytest tests/ -q
```

**验 `--json` 必须真的 `json.load` 一遍**，别只肉眼看开头 ——  P10 那个 bug 输出的是合法 JSON
`{"error": "..."}`，肉眼扫过去像正常错误处理，实际是持仓查询全挂。

## 项目侧依赖面（官方 common.py 改名/删函数时这些会断）
`scripts/{market_data,stock_fetch,trade_history_sync,order_sync,nav_snapshot,iv_logger,implied_move,regime_score}.py` import：
`create_quote_context, create_trade_context, get_default_acc_id, get_default_security_firm,
parse_trd_env, RET_OK, KLType, SubType, AuType, safe_get, safe_float, safe_int, format_enum, safe_close, check_ret`
