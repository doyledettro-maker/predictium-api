"""Reading fields off Stripe objects the same way on every stripe-python version.

From stripe-python 15 a StripeObject is not a dict: `.get()` raises
AttributeError. Indexing and `in` work on every version, and on the plain
dicts tests use. Never call `.get()` on a Stripe object; use `field()`.
"""

from datetime import datetime, timezone
from typing import Any, Optional


def field(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    try:
        return obj[key] if key in obj else default
    except (KeyError, TypeError):
        return default


def period_end(sub: Any) -> Optional[datetime]:
    """The subscription's current period end.

    API versions from 2025-03-31 (the one stripe-python 12+ pins) moved
    current_period_end from the subscription onto each subscription item.
    Older versions keep it at the top. Read whichever is there.
    """
    ts = field(sub, "current_period_end")
    if ts is None:
        items = field(field(sub, "items"), "data") or []
        ends = [field(i, "current_period_end") for i in items]
        ends = [e for e in ends if e is not None]
        ts = max(ends) if ends else None
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
