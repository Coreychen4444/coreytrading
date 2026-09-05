"""Optional closing line for outbound notifications.

The operator may pin one reminder sentence that every message sent through
`quant/ops/tg_notify.py` ends with. It is configured, never invented: set
``TG_FOOTER_LINE`` in the environment. Unset means no footer, and every helper
below degrades to a no-op so callers need no special case.

Idempotent by contract: a body that already contains the sentence is returned
unchanged, so code and a narrator can both append it without duplicating it.
"""
import os

ENV_VAR = "TG_FOOTER_LINE"
PREFIX = "⚓ "
MOTTO = os.environ.get(ENV_VAR, "").strip()
MOTTO_LINE = PREFIX + MOTTO if MOTTO else ""


def configured() -> bool:
    return bool(MOTTO)


def has_motto(text: str) -> bool:
    return bool(MOTTO) and MOTTO in (text or "")


def with_motto(text: str) -> str:
    """Append the configured line after a blank line; unchanged when unset or already present."""
    text = text or ""
    if not MOTTO or has_motto(text):
        return text
    body = text.rstrip()
    return f"{body}\n\n{MOTTO_LINE}" if body else MOTTO_LINE
