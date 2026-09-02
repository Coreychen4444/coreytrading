"""ops — 编排与运维。

    pipeline        定时任务采集编排 (heartbeat / briefing / postclose 共用同一份顺序定义)
    card_autopaint  缺卡 → 后台异步建卡 (detached, 不阻塞调用方)
    tg_notify       Telegram 推送 (可选投递适配器)

依赖: core (+ subprocess 调用其余各层)。
"""
