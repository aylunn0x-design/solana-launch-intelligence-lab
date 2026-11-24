"""Validation stage: reject or flag observations before they enter the dataset.

Philosophy: a row is either trustworthy, or it is flagged, or it is rejected —
never quietly "fixed". Silent correction is how a dataset becomes untraceable.

Severities
----------
REJECT : the row cannot be stored meaningfully (no identity, impossible values).
FLAG   : the row is stored but carries a recorded concern (e.g. a timestamp
         slightly in the future, or cross-source disagreement).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.providers.types import PoolSnapshot

#: Tolerance for clock skew between a provider's clock and ours.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

#: Solana mainnet launched 2020-03-16; no pool can predate it.
SOLANA_GENESIS = datetime(2020, 3, 16, tzinfo=timezone.utc)

#: Base58 alphabet used by Solana addresses (no 0, O, I, l).
_BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

#: Liquidity above this is treated as implausible for any single Solana pool and
#: flagged for review rather than silently trusted.
IMPLAUSIBLE_LIQUIDITY_USD = 1e12


class Severity(str, Enum):
    REJECT = "reject"
    FLAG = "flag"


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    severity: Severity
    message: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue]

    @property
    def rejected(self) -> bool:
        return any(i.severity is Severity.REJECT for i in self.issues)

    @property
    def flags(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.FLAG]

    def summary(self) -> str:
        return "; ".join(f"{i.field}: {i.message}" for i in self.issues)


def looks_like_solana_address(value: str | None) -> bool:
    """Shape check only — not an on-chain existence check."""
    if not value or not isinstance(value, str):
        return False
    if not (32 <= len(value) <= 44):
        return False
    return all(ch in _BASE58 for ch in value)


def validate_snapshot(snap: PoolSnapshot, *, now: datetime | None = None) -> ValidationReport:
    """Apply validation rules to one normalized snapshot."""
    now = now or datetime.now(timezone.utc)
    issues: list[ValidationIssue] = []

    # --- identity: without these the row cannot be joined to anything ---
    if not looks_like_solana_address(snap.pool_address):
        issues.append(
            ValidationIssue(
                "pool_address", Severity.REJECT, f"not a plausible address: {snap.pool_address!r}"
            )
        )
    if not looks_like_solana_address(snap.base_token_mint):
        issues.append(
            ValidationIssue(
                "base_token_mint",
                Severity.REJECT,
                f"not a plausible mint: {snap.base_token_mint!r}",
            )
        )

    # --- launch timestamp ---
    created = snap.pool_created_at
    if created is not None:
        if created > now + CLOCK_SKEW_TOLERANCE:
            issues.append(
                ValidationIssue(
                    "pool_created_at", Severity.REJECT, f"is in the future ({created.isoformat()})"
                )
            )
        elif created < SOLANA_GENESIS:
            issues.append(
                ValidationIssue(
                    "pool_created_at",
                    Severity.REJECT,
                    f"predates Solana mainnet ({created.isoformat()})",
                )
            )

    # --- observation timestamp ---
    if snap.observed_at is not None and snap.observed_at > now + CLOCK_SKEW_TOLERANCE:
        issues.append(
            ValidationIssue("observed_at", Severity.FLAG, "observation time is ahead of our clock")
        )

    # --- numeric sanity ---
    for fname in ("liquidity_usd", "fdv_usd", "market_cap_usd", "price_usd"):
        val = getattr(snap, fname)
        if val is not None and val < 0:
            issues.append(ValidationIssue(fname, Severity.REJECT, f"is negative ({val})"))

    for fname in ("volume_usd_m5", "volume_usd_h1", "volume_usd_h6", "volume_usd_h24"):
        val = getattr(snap, fname)
        if val is not None and val < 0:
            issues.append(ValidationIssue(fname, Severity.REJECT, f"is negative ({val})"))

    for fname in ("txns_h1", "txns_h24", "buys_h24", "sells_h24",
                  "unique_traders_h24", "buyers_h24", "sellers_h24"):
        val = getattr(snap, fname)
        if val is not None and val < 0:
            issues.append(ValidationIssue(fname, Severity.REJECT, f"is negative ({val})"))

    if snap.liquidity_usd is not None and snap.liquidity_usd > IMPLAUSIBLE_LIQUIDITY_USD:
        issues.append(
            ValidationIssue(
                "liquidity_usd", Severity.FLAG, f"implausibly large ({snap.liquidity_usd:.3g})"
            )
        )

    # --- internal consistency ---
    # Unique traders cannot exceed the number of transactions that produced them.
    if snap.unique_traders_h24 is not None and snap.txns_h24 is not None:
        if snap.unique_traders_h24 > snap.txns_h24:
            issues.append(
                ValidationIssue(
                    "unique_traders_h24",
                    Severity.FLAG,
                    f"exceeds txns_h24 ({snap.unique_traders_h24} > {snap.txns_h24})",
                )
            )

    # Volume windows are nested (m5 within h1 within h6 within h24), so a longer
    # window can never report less volume than a shorter one. Every ordered pair
    # is checked, not just adjacent ones: if an intermediate window is missing,
    # an adjacent-only check would let the violation through.
    volume_windows = ("volume_usd_m5", "volume_usd_h1", "volume_usd_h6", "volume_usd_h24")
    for i, short in enumerate(volume_windows):
        sv = getattr(snap, short)
        if sv is None:
            continue
        for long in volume_windows[i + 1:]:
            lv = getattr(snap, long)
            if lv is not None and sv > lv * 1.01:  # 1% tolerance for sampling skew
                issues.append(
                    ValidationIssue(
                        long, Severity.FLAG, f"{long} ({lv:.6g}) < {short} ({sv:.6g})"
                    )
                )

    return ValidationReport(issues=issues)


# ---------------------------------------------------------------------------
# Cross-source agreement
# ---------------------------------------------------------------------------


@dataclass
class AgreementResult:
    field: str
    value_a: float | None
    value_b: float | None
    relative_difference: float | None
    agrees: bool
    note: str = ""


def compare_sources(
    a: PoolSnapshot, b: PoolSnapshot, *, tolerance: float = 0.25
) -> list[AgreementResult]:
    """Compare the same pool as reported by two providers.

    Used to detect a provider going stale or changing units. A relative
    difference above `tolerance` is disagreement. The default 25% is loose on
    purpose: the two APIs sample at different instants, and new-token liquidity
    genuinely moves fast, so a tight bound would flag normal behaviour.
    """
    results: list[AgreementResult] = []
    for fname in ("liquidity_usd", "price_usd", "volume_usd_h24"):
        va, vb = getattr(a, fname), getattr(b, fname)
        if va is None or vb is None:
            results.append(
                AgreementResult(fname, va, vb, None, True, "not comparable: a value is missing")
            )
            continue
        denom = max(abs(va), abs(vb))
        if denom == 0:
            results.append(AgreementResult(fname, va, vb, 0.0, True, "both zero"))
            continue
        rel = abs(va - vb) / denom
        results.append(
            AgreementResult(
                fname, va, vb, rel, rel <= tolerance,
                "" if rel <= tolerance else f"differ by {rel:.1%}",
            )
        )
    return results
