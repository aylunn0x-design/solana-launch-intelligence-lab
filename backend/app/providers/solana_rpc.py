"""Solana JSON-RPC provider — holder concentration. DEGRADED: see warning.

Verified live against https://api.mainnet-beta.solana.com:
  * getVersion                -> HTTP 200 (endpoint reachable, solana-core 4.4.0)
  * getTokenLargestAccounts   -> HTTP 429 "Too many requests for a specific RPC call"
                                 on the FIRST call, not under load.

The public endpoint rate-limits exactly the method needed for holder
concentration. This provider is therefore implemented but **disabled by default**
(`ENABLE_HOLDER_COLLECTION=false`). When it cannot get data it raises
`HolderDataUnavailable`, which callers record as unavailable. It never estimates,
interpolates or substitutes holder numbers.

`getTokenLargestAccounts` returns at most the top 20 accounts, so a true holder
count and a true Gini coefficient are not obtainable from it at all. Only
top-1/top-10 share and a partial HHI over the returned accounts are computable,
and `holder_count` is left None rather than reported as 20.

To enable this properly, point SOLANA_RPC_URL at a dedicated provider
(Helius/Triton/QuickNode or similar). That requires an API key and possibly
payment, which is the project owner's decision.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.providers.base import ProviderError, RateLimitedClient
from app.providers.parsing import dig, to_float
from app.providers.types import HolderStats

logger = logging.getLogger(__name__)

SOURCE_NAME = "solana_rpc"
DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"

#: getTokenLargestAccounts caps its result at 20 accounts.
LARGEST_ACCOUNTS_CAP = 20


class HolderDataUnavailable(ProviderError):
    """Holder data could not be obtained. Callers must report it as missing."""


class SolanaRpcProvider:
    name = SOURCE_NAME
    requires_api_key = False

    def __init__(self, client: RateLimitedClient, rpc_url: str = DEFAULT_RPC_URL):
        self.client = client
        self.base_url = rpc_url

    @classmethod
    def build(cls, *, rate_per_min: int, rpc_url: str = DEFAULT_RPC_URL,
              timeout: float = 25.0, max_retries: int = 2) -> "SolanaRpcProvider":
        client = RateLimitedClient(
            rpc_url,
            rate_per_min=rate_per_min,
            timeout=timeout,
            max_retries=max_retries,
            headers={"Content-Type": "application/json"},
        )
        return cls(client, rpc_url=rpc_url)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _rpc(self, method: str, params: list | None = None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        resp = await self.client.get_json("", method="POST", json_body=body)
        payload = resp.payload
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(err)
            if code == 429:
                raise HolderDataUnavailable(
                    f"{method}: rate limited by the public RPC endpoint ({msg})",
                    status_code=429,
                )
            raise ProviderError(f"{method} returned RPC error {code}: {msg}")
        return payload

    async def get_version(self) -> dict:
        payload = await self._rpc("getVersion")
        return dig(payload, "result", default={}) or {}

    async def fetch_holder_stats(self, mint: str) -> HolderStats:
        """Partial holder concentration from the top accounts.

        Raises HolderDataUnavailable when the endpoint throttles, which is the
        expected outcome on the public RPC.
        """
        supply_payload = await self._rpc("getTokenSupply", [mint])
        total_supply = to_float(dig(supply_payload, "result", "value", "uiAmountString"))
        if total_supply is None:
            total_supply = to_float(dig(supply_payload, "result", "value", "uiAmount"))

        largest = await self._rpc("getTokenLargestAccounts", [mint])
        accounts = dig(largest, "result", "value", default=None)
        if not isinstance(accounts, list) or not accounts:
            raise HolderDataUnavailable(f"no largest-account data returned for {mint}")

        amounts: list[float] = []
        for acc in accounts:
            amt = to_float(dig(acc, "uiAmountString")) or to_float(dig(acc, "uiAmount"))
            if amt is not None and amt > 0:
                amounts.append(amt)

        if not amounts:
            raise HolderDataUnavailable(f"largest accounts for {mint} had no parseable amounts")

        amounts.sort(reverse=True)
        denominator = total_supply if total_supply and total_supply > 0 else sum(amounts)
        if denominator <= 0:
            raise HolderDataUnavailable(f"cannot compute shares for {mint}: supply unknown")

        top1 = amounts[0] / denominator
        top10 = sum(amounts[:10]) / denominator
        # HHI over the accounts we can see. Partial by construction: the API
        # returns at most 20 accounts, so this understates concentration
        # dispersion across the full holder base.
        shares = [a / denominator for a in amounts]
        hhi = sum(s * s for s in shares)

        return HolderStats(
            mint=mint,
            observed_at=datetime.now(timezone.utc),
            # Deliberately None: we cannot know the true holder count from a
            # capped top-20 list, and reporting 20 would be a fabricated number.
            holder_count=None,
            top1_share=min(top1, 1.0),
            top10_share=min(top10, 1.0),
            herfindahl_index=hhi,
            total_supply=total_supply,
        )
