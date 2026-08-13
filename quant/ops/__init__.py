"""ops — 编排与运维。

    pipeline        定时任务采集编排 (heartbeat / briefing 共用同一份顺序定义)
    tg_notify       Telegram 推送
    commit_state.sh 定时任务后自动提交 state
    sync_memory.sh  memory 目录 → 私有 repo 镜像

依赖: core (+ subprocess 调用其余各层)。
"""
