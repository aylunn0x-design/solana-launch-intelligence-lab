# Research Plan

The study design. Written before conclusions, so that what was planned can be
distinguished from what was found.

> **Standing caveat.** Every result this platform produces is an association
> measured in observational data. There is no randomisation and no control over
> which tokens launch, so nothing here can establish causation.

---

## 1. Central question

> Which observable characteristics of newly launched Solana tokens are
> associated with continued liquidity, market activity and survival, and how
> robust are those relationships across different market conditions?

### Population

AMM pools created on Solana, observed from creation onward. The unit is a
**pool**, not a token: a launch is the creation of a market, and a token with
several pools has several lifecycles.

### Two samples, with different bias properties

| Sample | How built | Bias |
|---|---|---|
| **Inception cohort** | Every pool seen in the `new_pools` feed, polled on a schedule | None from selection: a pool is captured at birth regardless of what happens to it afterwards |
| **Retrospective sample** | Pools found in ranked "top"/"trending" listings | **Survivorship-biased**: a pool appears only because it is *currently* notable, so pools that launched and died are systematically missing |

Inferential claims about launch outcomes require the inception cohort. The
retrospective sample is for exploratory and descriptive work, and every result
computed over it carries that warning.

**This distinction is forced by the data source, not chosen.** The `new_pools`
feed stops at page 10 (page 11 returns HTTP 401), covering roughly the last six
minutes of launches, so an unbiased cohort cannot be backfilled — only
accumulated.

---

## 2. Variables

### Exposures (known at launch — the only legal model inputs)

| Variable | Definition |
|---|---|
| `initial_liquidity_usd` | Pool reserve in USD at the earliest observation |
| `initial_fdv_usd` | Fully diluted valuation at the earliest observation |
| `first_hour_volume_usd` | Volume in the first hourly candle at/after creation |
| `first_hour_unique_traders` | Distinct trading addresses, first hour (null unless observed in-window) |
| `first_hour_txns` | Transactions, first hour (same caveat) |
| `launch_hour_utc`, `launch_dow` | Launch timing |
| `dex` | Venue hosting the pool |

### Outcomes

| Variable | Definition |
|---|---|
| `duration_hours` | Hours from creation to last observed activity (event) or to now (censored) |
| `event_observed` | Whether inactivity was observed, as opposed to censoring |
| `survived_24h` / `survived_72h` | Three-valued: true / false / **undetermined** |

### Descriptive (forward-looking — never model inputs)

`peak_liquidity_usd`, `liquidity_cv`, `total_volume_usd`, `active_hours`,
`activity_persistence`, `realized_volatility`, `max_drawdown_pct`.

---

## 3. Outcome definition

```
last_activity = latest hourly candle with volume > 0

if (now - last_activity) > inactivity_threshold:
    event observed;  duration = last_activity - pool_created_at
else:
    right-censored; duration = now - pool_created_at
```

Three deliberate choices:

1. **Activity, not "rug" or "death".** Volume ceasing is observable; intent is
   not. The platform measures what the data contains.
2. **Censoring is handled, not dropped.** Most pools are still alive when
   observed. Discarding them would bias the sample toward failures; counting
   them as survivors-forever would bias it the other way.
3. **The threshold is a parameter.** It is recorded per dataset version because
   it materially changes the measured outcome (see RQ6).

### The undetermined case

A pool still active but younger than the horizon has **not** failed to survive
to it. Such pools are labelled `None`, not `false`, and are excluded from
classification. Labelling them `false` would count every recent launch as a
failure and make any survival rate a function of when collection started.

---

## 4. Research questions

### RQ1 — Is initial liquidity associated with longer measurable activity?

- **H1:** Pools with higher initial liquidity remain active longer.
- **Variables:** `initial_liquidity_usd` → `duration_hours`, `event_observed`.
- **Method:** Spearman correlation; Kaplan-Meier stratified by liquidity
  quartile; Cox with log1p(initial liquidity).
- **Confounders:** liquidity correlates with how long a pool has existed, and
  longer-lived pools have more opportunity to be observed. Pools from ranked
  listings are liquid *because* they survived — RQ1 is only answerable on the
  inception cohort.

### RQ2 — Is holder concentration associated with liquidity stability?

**Out of scope in the MVP — blocked, not dropped.** The public Solana RPC
rate-limits `getTokenLargestAccounts` and caps it at 20 accounts. Requires a
dedicated RPC provider. The table, provider and UI path exist; the UI reports
holder data as unavailable.

### RQ3 — How does activity distribution relate to the lifecycle?

- **H3:** Pools whose activity is spread across many hours (high
  `activity_persistence`) remain active longer than pools with one burst.
- **Method:** Spearman; group comparison between high/low persistence.
- **Caveat:** both measures derive from the same volume series, so the
  relationship is partly mechanical. Descriptive only.

### RQ4 — Do launch cohorts differ statistically?

- **H4:** Pools launched in different hours-of-day, or on different venues,
  differ in survival.
- **Method:** Kaplan-Meier stratified; multivariate log-rank; Mann-Whitney on
  continuous outcomes.
- **Caveat:** venue is confounded with token type — some venues host
  predominantly one kind of launch.

### RQ5 — Do relationships differ across market regimes?

**Deferred.** Requires a collection window measured in months. Results warn
automatically when launch dates span less than a week.

### RQ6 — How much do results change when the survival definition changes?

- **Method:** build dataset versions at several inactivity thresholds and
  compare event rates and median survival.
- **Status: answered, and the answer matters.** On the development sample the
  observed event rate moved from **12% at a 6h threshold to 4% at 12h to 0% at
  24h and 48h**, on the same pools. The measured "death rate" is substantially
  an artefact of the definition. Any statement about how often launches fail
  must quote its threshold.

### RQ7 — How sensitive are results to missing data?

- **Method:** report missing-data share with every statistic; compare complete
  cases against the full sample; exclude rather than impute.
- **Status:** partially answered. The platform detects the degenerate case where
  pools have a single observation — `peak_liquidity_usd` then equals
  `initial_liquidity_usd` by construction (they correlate at exactly 1.0) and
  `liquidity_cv` is undefined — and warns rather than presenting the tautology
  as a finding.

---

## 5. Analysis plan

1. **Descriptive first.** Distributions, medians, skewness, missingness. No
   modelling before the data is understood.
2. **Bivariate association.** Spearman by default; FDR-adjusted p-values for any
   matrix.
3. **Group comparison.** Mann-Whitney with rank-biserial effect size and a
   bootstrap interval for the median difference.
4. **Survival.** Kaplan-Meier, log-rank, then Cox — with the proportional-hazards
   assumption tested and the result reported.
5. **Modelling last, and only against a baseline.** Majority-class baseline,
   then logistic regression, then a shallow random forest. Chronological split.
   ROC-AUC and average precision lead; accuracy is shown next to the baseline's.

### Refusal thresholds

The platform declines rather than reporting a fragile number:

| Condition | Behaviour |
|---|---|
| n < 20 | No inferential statistic |
| Pairwise n < 10 | No correlation |
| Group n < 3 | No group comparison |
| 0 events | No survival curve |
| < 10 events | No Cox model |
| < 40 determined outcomes, or minority class < 5 | No classifier |

---

## 6. Threats to validity

| Threat | Mitigation |
|---|---|
| Survivorship bias | Discovery method recorded per pool; warnings on every result; inception cohort kept separate |
| Look-ahead bias | Features classified by availability; gate raises on violation; chronological split |
| Censoring mishandled | Survival analysis throughout; three-valued binary labels |
| Data snooping | FDR adjustment on matrices; refusal thresholds; hypotheses stated here in advance |
| Definition sensitivity | RQ6 built into the platform as a first-class view |
| Provider error | Second source for cross-validation; validation rejects impossible values and flags suspicious ones |
| Short-lived pools invisible | Documented: the hourly grid plus the minimum-candle rule excludes the shortest-lived pools, biasing toward longer-lived tokens |
| Non-independence | A token with several pools contributes several rows; `token_id` retained so this can be modelled later |

---

## 7. What would change the conclusions

Stated in advance, so the analysis cannot be quietly rescued later:

- If the inception cohort shows a **different** liquidity-survival relationship
  from the retrospective sample, the retrospective result was bias, not signal.
- If RQ1's association disappears once pool age is controlled for, it was
  confounded by observation duration.
- If the event rate keeps moving with the inactivity threshold once the sample
  is larger, "survival" is not a stable construct on this data and should be
  reported only as a threshold-conditional quantity.
