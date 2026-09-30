"""Normalize OpenD discrete report types without accepting cumulative periods."""


def discrete_quarter(value) -> int | None:
    """SDK 10.10 returns F10Type strings; older captures contain proto integers.

    Only the four discrete quarters qualify. Never infer a quarter from a
    period label or turn a cumulative/annual report into a quarterly flow.
    """
    if type(value) is int and 1 <= value <= 4:
        return value
    if isinstance(value, str):
        return {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}.get(value)
    return None
