# Data Sources

Status of every external source considered for this project.

**Verification policy.** A capability is marked **VERIFIED** only if a live request was
made from this project and the response inspected. Anything taken from documentation
without a successful live call is marked **DOCUMENTED (unverified)**. Anything I could
not establish is marked **UNKNOWN** and is not relied upon.

Last verification run: 2026-11-24.

---

## 1. GeckoTerminal public API — PRIMARY SOURCE

Base URL: `https://api.geckoterminal.com/api/v2`
Auth: none required (no API key) — **VERIFIED**

### Endpoints verified by live request

| Endpoint | Result | What it gives us |
|---|---|---|
| `GET /networks` | HTTP 200 — **VERIFIED** | Network id list (`solana`, `eth`, …) |
| `GET /networks/solana/new_pools?page=N` | HTTP 200, 20 pools/page — **VERIFIED** | **Launch discovery.** Newly created pools |
| `GET /networks/solana/pools/{pool}/ohlcv/hour?aggregate=1&limit=1000` | HTTP 200, **1000 candles** — **VERIFIED** | Hourly OHLCV history (~41 days in one call) |
| `GET /networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit=100` | HTTP 200 — **VERIFIED** | Minute OHLCV, for pools minutes old |

### Fields confirmed present on `new_pools` (inspected live payload)

- `address` — pool address
- `name` — e.g. `"TOKEN / SOL"`
- `pool_created_at` — ISO-8601 UTC launch timestamp. **This is the launch clock for the study.**
- `reserve_in_usd` — pool liquidity in USD
- `fdv_usd`, `market_cap_usd` (`market_cap_usd` is frequently `null` for new tokens — **VERIFIED**)
- `base_token_price_usd`, `quote_token_price_usd`, and native-currency equivalents
- `price_change_percentage` — `{m5, m15, m30, h1, h6, h24}`
- `volume_usd` — `{m5, m15, m30, h1, h6, h24}`
- `transactions` — per window `{buys, sells, buyers, sellers, unique_traders}`
  → gives genuine **unique participant counts**, not just transaction counts
- `relationships` — `base_token`, `quote_token` (ids like `solana_<mint>`), `dex` (e.g. `pumpswap`)

### OHLCV response shape — VERIFIED

`data.attributes.ohlcv_list` = array of `[unix_ts, open, high, low, close, volume]`,
returned newest-first. Max observed rows per call: **1000**.

### Rate limit — EMPIRICALLY MEASURED, not documented here

HTTP 429 observed after a small burst (~6 calls within a minute), body:
`"You've exceeded the Rate Limit."` The free tier is strict. The documented
per-minute figure is **UNKNOWN** to this project: `www.geckoterminal.com` returned
HTTP 403 to our client, and the DexScreener/CoinGecko docs are JavaScript-rendered
pages whose text loads from a CDN host that is not reachable from the build
environment.

**Consequence for design:** the limiter does not depend on knowing the exact number.
`providers/base.py` implements a token-bucket limiter with a conservative configurable
default plus adaptive backoff that honours HTTP 429 and `Retry-After`. See
`GECKOTERMINAL_RATE_LIMIT_PER_MIN` in `.env.example`.

---

## 2. DexScreener API — SECONDARY / CROSS-VALIDATION SOURCE

Base URL: `https://api.dexscreener.com`
Auth: none required — **VERIFIED**

| Endpoint | Result |
|---|---|
| `GET /latest/dex/tokens/{mint}` | HTTP 200, 30 pairs for wSOL — **VERIFIED** |

Fields confirmed live: `pairAddress`, `dexId`, `baseToken{address,name,symbol}`,
`quoteToken{...}`, `priceUsd`, `priceNative`, `liquidity{usd,base,quote}`,
`volume{m5,h1,h6,h24}`, `txns{m5,h1,h6,h24}{buys,sells}`,
`priceChange{m5,h1,h6,h24}`, `pairCreatedAt` (epoch **milliseconds** — note the unit
differs from GeckoTerminal's ISO string), `fdv`, `marketCap`, `info{websites,socials}`.

**Why keep a second source:** it provides an *independent* measurement of liquidity and
volume for the same pool. The project uses this for cross-source agreement checks
(`pipeline/validate.py`), which is how we detect a provider silently changing units or
returning stale values. It does **not** supply `unique_traders`, so GeckoTerminal
remains primary.

Documented rate limits: **UNKNOWN** (see above — docs page is client-rendered).
Treated with the same adaptive limiter.

---

## 3. CoinGecko API — FALLBACK

`GET https://api.coingecko.com/api/v3/ping` → HTTP 200 — **VERIFIED**

CoinGecko hosts GeckoTerminal-derived on-chain endpoints. Registered as a fallback
provider so the pipeline is not captive to one vendor, but **not** used as a primary
path in the MVP, and its on-chain endpoints are **not yet verified** by live call.

---

## 4. Solana JSON-RPC (public endpoint) — DEGRADED, NOT RELIED UPON

`POST https://api.mainnet-beta.solana.com`

| Method | Result |
|---|---|
| `getVersion` | HTTP 200, `solana-core 4.4.0` — **VERIFIED reachable** |
| `getTokenLargestAccounts` | **HTTP 429** `"Too many requests for a specific RPC call"` — **VERIFIED BLOCKED** |

This is an important negative result. The public RPC endpoint rate-limits exactly the
expensive call needed for **holder concentration**. It failed on the *first* attempt,
not under load.

**Consequence for research scope — read this before trusting any holder metric.**
Holder-concentration features (top-holder share, holder Gini/HHI) are **not populated**
in the MVP. The provider interface for them exists and is implemented, but it is
disabled by default and the UI reports holder data as *unavailable* rather than
guessing. Research question RQ2 ("is holder concentration associated with liquidity
stability?") is therefore **declared out of scope for the MVP** and documented as
blocked, not silently dropped. Enabling it requires a dedicated RPC provider
(Helius / Triton / QuickNode or similar), which needs an API key and possibly payment —
a decision for the project owner, not something to assume.

---

## 5. Sources considered and rejected

| Source | Why not |
|---|---|
| Dune Analytics | Query API requires an account and API key; free tier credit-limited. Not usable without the owner provisioning credentials. |
| Bitquery / Birdeye / Helius | All require API keys. Birdeye's useful history is paid. Kept as future options in the provider registry. |
| Raw on-chain backfill via `getSignaturesForAddress` | Correct in principle, but the public RPC rate limits above make a historical backfill infeasible without a paid node. |

---

## 6. Terms of use — OPEN ITEM, flagged deliberately

I was **unable to verify** the terms of use / attribution requirements for GeckoTerminal
and DexScreener from this environment (403 and client-rendered docs respectively).

This project stores API responses in a local database for reproducible research. That
is normal research caching, but **before publishing a dataset derived from these APIs,
or deploying this publicly, the project owner should read each provider's current terms
and attribution requirements.** This is recorded in `TODO.md` as a release blocker
rather than assumed to be fine. The platform attributes every stored row to its source
(`data_sources` table + `source_id` foreign keys), so attribution is technically
possible once the requirements are known.
