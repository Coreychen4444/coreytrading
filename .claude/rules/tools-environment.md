# Tools & Environment Reference

## Secrets
- 本地配置从 `.env.example` 复制为被忽略的 `.env`；账户标识、登录 hash、token、私钥、券商导出一律不入库。
- `opend/OpenD.xml.reference` 只是占位模板；填了真实值的 OpenD 配置留在仓库之外（流程见 `opend/README.md`）。
- 通知凭据放 `~/.config/coreytrading/telegram.env` 或环境变量；可选尾行 `TG_FOOTER_LINE`。

## Moomoo OpenD
- Install：官方 CLI / GUI 发行版；目录名 ≠ 版本号（自更新原地覆盖），真实版本看启动 banner 或 OpenD 的 `GTWLog_*.log`。
- Config：安装目录内的 `OpenD.xml`。🚨 自更新可能把它还原成出厂模板（account 占位符 + RSA 注释掉 → 起不来 = 所有定时任务静默变黑）。判据 `diff <(tr -d '\r' < OpenD.xml.default) <(tr -d '\r' < OpenD.xml)` 为空；恢复用 `opend/OpenD.xml.reference` + 手填 md5；改 live 后同步回本地 reference。
- Run：手动 `<install_dir>/OpenD.app/Contents/MacOS/OpenD --cfg_file=<install_dir>/OpenD.xml`（无 auto-start；常驻用 nohup / tmux）。Client 默认 `127.0.0.1:11111`；listener 只绑定本机，除非 operator 明确设计并保护远程访问。
- Encryption：启用时 RSA 1024 PKCS#1；单一 key（如 `~/.com.moomoo.OpenD/rsa/private_pkcs1.pem`），OpenD.xml 与 `.env` 的 `FUTU_OPEND_RSA_KEY` 同指一个本地绝对路径，不在安装目录放副本，不入库。
- `.env`（workspace root）：`FUTU_OPEND_HOST` + `FUTU_OPEND_ENCRYPT=1` + `FUTU_OPEND_RSA_KEY`。
- SDK：`moomoo-api` 与 OpenD 同 big-version（patch 号不用对齐；pin 在 `pyproject.toml` / `requirements.txt`）。升级 OpenD 后在隔离环境 `pip install --upgrade moomoo-api==<ver>` **并改 pin**。`moomoo.common` 缺 → `--force-reinstall --no-deps`。
- **Python：所有 moomoo 脚本一律用隔离环境的解释器**（系统 python 有 protobuf 冲突）。
- 底层脚本 `.claude/skills/openapi/scripts/`（官方包 + 本地补丁，升级照 `.claude/skills/openapi/LOCAL_PATCHES.md` 重打）；业务代码 `quant/`（分层真源 `quant/README.md`；OpenD 的 `common` 只走 `core.paths.add_openapi_path()`）。
- **报告货币**：非港币计价账户跑 `get_portfolio.py` 必须 `--currency USD`，否则服务端按 HKD 换算。
- OpenD 未启动 → 连接拒绝，提醒 operator 启动；pipeline 自带 OpenD-down 告警与冷却。
- 券商连接类命令是 smoke test，不进离线测试；`.claude/skills/openapi/scripts/trade/` 下的订单脚本不在 pipeline 内，任何改单动作先取 operator 的显式授权，永不通过 SDK 解锁交易。

## Data sources（价格永远 OpenD / kline，禁 WebSearch 查行情）
1. OpenD 实时；2. `positions.json` `broker` 快照（`stock_fetch.py` 刷新）；3. 本地缓存 `market/klines/<T>.jsonl` + `market/iv_history/<T>.jsonl` + VIX via `quant/data/macro_fetch.py`；4. 新闻 / 宏观叙事只读 operator 配置的情报归档（可选 `trade/intelligence/reports/`），未配置 = 无输入。

## 时区
- `events/*/trades.jsonl` 时间戳 = 带 ET offset 的 ISO；券商订单 `create_time` = ET。SGT = ET + 12h（夏令）/ +13h（冬令）；下结论前先换算。
- ET 09:30–16:00 正常交易；04:00–09:30 pre-market；16:00–20:00 after-hours。30 分钟规则只适用 ET 09:30–10:00。
- 交易日、半日与 DST 的唯一裁定 = `quant/core/market_sessions.py` 的 XNYS 日历；不用星期几猜。

## 模型运行器（可选）
- 领域角色的运行器是 Claude Code 与 Codex CLI（`agents/routing.json`）；二进制解析顺序 `CLAUDE_BIN` / `CODEX_BIN` → PATH → 已知安装位置，`python3 quant/ops/agent_doctor.py --json` 只按路径报告，不执行。
- Worker 无工作区、券商、shell、消息与下单权限；运行产物、租约、备份在被忽略的 `var/`。
