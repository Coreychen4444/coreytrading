# 模型默认值与维护授权

本文件记录模型选型与后续维护的用户授权；具体执行配置以 `routing.json` 为准。它面向维护宿主，不作为研究 worker 的任务指令，也不改变交易权限。

## 默认分工

用户指定的初始偏好：

| 模型提供方 | 运行器 | 首选 | 次选 | effort |
|---|---|---|---|---|
| Anthropic | Claude Code | `claude-fable-5-1` | `claude-opus-5` | `xhigh`，对应用户所述 extra／extra high |
| OpenAI | Codex CLI | `gpt-6-astra` | `gpt-5.6-sol` | `high` |

宏观、估值默认使用 OpenAI；技术、风控／仓位管理默认使用 Anthropic。故障备用先尝试同一模型提供方的次选；另一家的首选与次选用于独立复核。有效弃权、拒绝提案或复核不批准都不构成换模型重问的理由。

`model_profiles` 集中定义运行器、首选／次选模型与 effort，各角色引用 profile。新增 profile 可以为某个角色单独选择模型；共享 profile 的升级会同时作用于所有引用它的主用及复核端点。

## 后续模型迭代

用户授权：随着各模型厂商迭代，可自行更新默认模型，无需为同一职责范围内的正常版本升级重复询问。此授权是维护权限，本身不创建定时检查，也不意味着每次运行都自动使用 latest。

每次升级应：

1. 核对厂商官方文档中的完整模型 ID、effort 参数、结构化输出能力和当前 CLI 支持，保留来源及检查时间。模型名称或推荐列表不证明本账户可用。
2. 保持既定角色分工、同厂商首选／次选顺序、跨厂商复核、worker 隔离与交易权限边界。effort 默认保持用户指定强度；厂商改名时根据官方语义与实际 CLI 行为映射。
3. 在独立运行目录内验证新模型的可用性、契约输出和必要的代表性研究案例，遵守现有全局调用额度；不以生产卡片写入作为探针。
4. 运行与改动相称的契约、备用、复核、额度及提交边界检查，记录耗时、用量、请求模型、可观察的实际模型信息，以及未能确认的字段。
5. 备份原配置，校验候选配置后原子替换；同步运行手册与根 README。若新模型不可用或出现回归，保留或恢复已验证的配置，并留下失败记录。

运行中的一次任务使用启动时解析的路由，避免研究中途静默换版本；新任务读取更新后的配置。厂商返回的模型 ID 或 effort 不可观察时，记录未报告，不把请求参数伪装成服务端确认。测试与模型一致意见不代表投资收益或研究质量已被证明。

## 官方依据

- [Claude Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview)
- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Codex 模型选择](https://learn.chatgpt.com/docs/models)
- [GPT-6 Astra 指南](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)
- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
