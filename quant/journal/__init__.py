"""journal — 把决策与成交写进账本 (prose-only 不算落库)。

    annotate_event  fill/close 事件补 tier / classification / score
    decision_log    EV / Kelly worksheet → decisions.jsonl

anonymize_map.tsv = 公开 vault 同步用的脱敏映射表 (trading-journal skill 读)。
依赖: core。
"""
