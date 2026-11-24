"""Safe coercion helpers.

Market APIs return numbers as strings, sometimes with 60+ significant digits,
sometimes null, occasionally as the string "None". Parsing must never raise on
unexpected input and must never silently turn a missing value into 0 — a missing
observation and a zero observation mean completely different things in the
analysis, so a failed parse yields None and is recorded as missing.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def to_float(value: Any) -> float | None:
    """Coerce to float, or None. Rejects NaN/inf so they never reach the DB."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"none", "null", "nan", "-"}:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    f = to_float(value)
    if f is None:
        return None
    try:
        return int(f)
    except (ValueError, OverflowError):
        return None


def to_utc_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string or epoch number into a timezone-aware UTC datetime.

    Handles both provider conventions seen in this project:
      * GeckoTerminal: ISO-8601 string, e.g. "2026-11-24T22:25:01Z"
      * DexScreener:   epoch **milliseconds**, e.g. 1688106058000
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return from_epoch(value)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string -> epoch
        if s.lstrip("-").isdigit():
            return from_epoch(float(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    return None


def from_epoch(value: float) -> datetime | None:
    """Interpret a number as epoch seconds or milliseconds.

    Disambiguated by magnitude: values above 1e11 are milliseconds. (1e11
    seconds is year 5138, so no realistic second-timestamp reaches it, and
    1e11 ms is 1973 — safely below any pool creation date.)
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 1e11:
        v /= 1000.0
    try:
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def strip_network_prefix(token_id: Any, network: str = "solana") -> str | None:
    """GeckoTerminal returns token ids as "solana_<mint>". Return the bare mint."""
    if not isinstance(token_id, str) or not token_id:
        return None
    prefix = f"{network}_"
    return token_id[len(prefix):] if token_id.startswith(prefix) else token_id


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """Walk nested dicts without raising on a missing or non-dict level."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur if cur is not None else default
