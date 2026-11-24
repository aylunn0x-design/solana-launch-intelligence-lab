/**
 * API client.
 *
 * Two deliberate choices:
 *
 * 1. **Responses are validated with Zod before use.** The backend is a separate
 *    service that can be redeployed independently; if a field disappears or
 *    changes shape, the UI should say so loudly rather than render `undefined`
 *    as a plausible-looking blank.
 *
 * 2. **A failed request is never silently replaced with sample data.** On
 *    failure the caller receives an `ApiError` and renders an error state. This
 *    is the whole reason the project does not ship a "demo mode" that quietly
 *    substitutes fabricated numbers.
 */

import { z } from "zod";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  readonly status: number | null;
  readonly detail: string;
  readonly url: string;

  constructor(message: string, opts: { status?: number | null; detail?: string; url: string }) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status ?? null;
    this.detail = opts.detail ?? message;
    this.url = opts.url;
  }

  /** True when the backend is unreachable rather than returning an error. */
  get isUnreachable(): boolean {
    return this.status === null;
  }
}

export class SchemaError extends Error {
  readonly issues: string[];
  readonly url: string;

  constructor(url: string, issues: string[]) {
    super(
      `The API response did not match the expected shape (${url}). ` +
        `This usually means the backend and frontend versions differ.`,
    );
    this.name = "SchemaError";
    this.issues = issues;
    this.url = url;
  }
}

type QueryValue = string | number | boolean | null | undefined | string[];

function buildUrl(path: string, params?: Record<string, QueryValue>): string {
  const url = new URL(path.replace(/^\//, ""), `${API_BASE_URL.replace(/\/$/, "")}/`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      if (Array.isArray(value)) {
        for (const item of value) url.searchParams.append(key, String(item));
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

interface FetchOptions {
  params?: Record<string, QueryValue>;
  /** Seconds to cache on the server. 0 disables caching (live data). */
  revalidate?: number;
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

/** Fetch and validate, or throw. Never returns partial or invented data. */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  options: FetchOptions = {},
): Promise<T> {
  const url = buildUrl(path, options.params);
  const { method = "GET", body, revalidate = 0, signal } = options;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      // Research data changes as ingestion runs; stale caching would show
      // numbers that disagree with the database.
      cache: revalidate > 0 ? undefined : "no-store",
      next: revalidate > 0 ? { revalidate } : undefined,
    });
  } catch (cause) {
    throw new ApiError(
      `Could not reach the research API at ${API_BASE_URL}.`,
      {
        status: null,
        detail:
          cause instanceof Error
            ? `${cause.message}. Is the backend running? Start it with: uvicorn app.main:app --reload`
            : "Unknown network error.",
        url,
      },
    );
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail) detail = JSON.stringify(payload.detail);
    } catch {
      /* body was not JSON; keep the status line */
    }
    throw new ApiError(detail, { status: response.status, detail, url });
  }

  const json: unknown = await response.json();
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new SchemaError(
      url,
      parsed.error.issues.slice(0, 8).map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`),
    );
  }
  return parsed.data;
}

/* ------------------------------------------------------------------------- */
/* Schemas                                                                    */
/* ------------------------------------------------------------------------- */

/** Numeric fields are nullable throughout: "missing" is a real, distinct state. */
const num = z.number().nullable();
const int = z.number().int().nullable();

export const healthSchema = z.object({
  status: z.string(),
  environment: z.string(),
  database: z.object({
    reachable: z.boolean(),
    engine: z.string(),
    error: z.string().nullable(),
  }),
  data_sources: z.array(
    z.object({
      name: z.string(),
      enabled: z.boolean(),
      requires_api_key: z.boolean(),
    }),
  ),
  holder_collection_enabled: z.boolean(),
  disclaimer: z.string(),
});
export type Health = z.infer<typeof healthSchema>;

export const overviewSchema = z.union([
  z.object({
    dataset_empty: z.literal(true),
    message: z.string(),
    how_to_populate: z.string(),
    counts: z.object({
      tokens: z.number(),
      pools: z.number(),
      market_observations: z.number(),
      ohlcv_candles: z.number(),
    }),
  }),
  z.object({
    dataset_empty: z.literal(false),
    counts: z.object({
      tokens: z.number(),
      pools: z.number(),
      market_observations: z.number(),
      ohlcv_candles: z.number(),
      dataset_versions: z.number(),
      experiments: z.number(),
    }),
    launch_date_range: z.object({
      earliest: z.string().nullable(),
      latest: z.string().nullable(),
    }),
    cohort_composition: z.object({
      counts: z.record(z.string(), z.number()),
      inception_cohort_pools: z.number(),
      inception_cohort_share: num,
      note: z.string(),
    }),
    coverage: z.object({
      pools_with_history: z.number(),
      pools_with_history_share: num,
      pools_with_launch_timestamp: z.number(),
      pools_with_launch_timestamp_share: num,
      observations_with_unique_traders: z.number(),
      unique_trader_coverage_share: num,
    }),
    ingestion: z.object({
      last_successful_at: z.string().nullable(),
      last_successful_job_type: z.string().nullable(),
      failed_jobs: z.number(),
      total_jobs: z.number(),
    }),
    distributions: z.object({
      liquidity_usd: z.object({
        min: num,
        mean: num,
        max: num,
        non_null_observations: z.number(),
        note: z.string(),
      }),
      txns_h24: z.object({
        mean: num,
        max: num,
        non_null_observations: z.number(),
      }),
    }),
    dataset_versions: z.array(
      z.object({
        id: z.number(),
        label: z.string(),
        pool_count: z.number(),
        candle_count: z.number(),
        inception_cohort_share: num,
        inactivity_threshold_hours: z.number(),
        built_at: z.string().nullable(),
      }),
    ),
    recent_experiments: z.array(
      z.object({
        id: z.number(),
        name: z.string(),
        method: z.string(),
        created_at: z.string().nullable(),
      }),
    ),
    data_quality_warnings: z.array(
      z.object({ severity: z.string(), code: z.string(), message: z.string() }),
    ),
    generated_at: z.string(),
  }),
]);
export type Overview = z.infer<typeof overviewSchema>;

const observationSummarySchema = z
  .object({
    observed_at: z.string(),
    liquidity_usd: num,
    price_usd: num,
    fdv_usd: num,
    volume_usd_h24: num,
    txns_h24: int,
    unique_traders_h24: int,
    price_change_pct_h24: num,
    missing_field_count: z.number(),
  })
  .nullable();

export const tokenListSchema = z.object({
  items: z.array(
    z.object({
      pool_address: z.string(),
      mint: z.string(),
      symbol: z.string().nullable(),
      name: z.string().nullable(),
      dex: z.string().nullable(),
      pool_name: z.string().nullable(),
      pool_created_at: z.string().nullable(),
      age_hours: num,
      discovery_method: z.string(),
      survivorship_biased: z.boolean(),
      latest: observationSummarySchema,
      data_completeness: z.object({
        observations: z.number(),
        candles: z.number(),
        has_launch_timestamp: z.boolean(),
        has_history: z.boolean(),
      }),
    }),
  ),
  pagination: z.object({
    page: z.number(),
    page_size: z.number(),
    total: z.number(),
    total_pages: z.number(),
    has_next: z.boolean(),
  }),
  filters_applied: z.record(z.string(), z.unknown()),
});
export type TokenList = z.infer<typeof tokenListSchema>;

export const tokenDetailSchema = z.object({
  token: z.object({
    mint: z.string(),
    symbol: z.string().nullable(),
    name: z.string().nullable(),
    first_seen_at: z.string().nullable(),
  }),
  pool: z.object({
    pool_address: z.string(),
    pool_name: z.string().nullable(),
    network: z.string(),
    dex: z.string().nullable(),
    quote_token_mint: z.string().nullable(),
    pool_created_at: z.string().nullable(),
    age_hours: num,
    discovery_method: z.string(),
    survivorship_biased: z.boolean(),
    discovered_at: z.string().nullable(),
  }),
  other_pools_for_token: z.array(
    z.object({
      pool_address: z.string(),
      dex: z.string().nullable(),
      pool_created_at: z.string().nullable(),
    }),
  ),
  observation_summary: z.object({
    count: z.number(),
    first_observed_at: z.string().nullable(),
    last_observed_at: z.string().nullable(),
  }),
  latest: observationSummarySchema,
  candle_counts: z.record(z.string(), z.number()),
  holder_snapshots: z.array(
    z.object({
      observed_at: z.string(),
      top1_share: num,
      top10_share: num,
      herfindahl_index: num,
      holder_count: int,
    }),
  ),
  holder_data_available: z.boolean(),
  missing_data_warnings: z.array(z.string()),
  provenance: z.object({
    discovered_via_source: z.string().nullable(),
    observation_sources: z.array(
      z.object({ source: z.string(), observations: z.number() }),
    ),
    note: z.string(),
  }),
});
export type TokenDetail = z.infer<typeof tokenDetailSchema>;

export const tokenHistorySchema = z.object({
  pool_address: z.string(),
  pool_created_at: z.string().nullable(),
  timeframe: z.string(),
  liquidity_activity_series: z.array(
    z.object({
      observed_at: z.string(),
      liquidity_usd: num,
      price_usd: num,
      fdv_usd: num,
      volume_usd_h24: num,
      txns_h24: int,
      unique_traders_h24: int,
      buys_h24: int,
      sells_h24: int,
      source_id: z.number(),
    }),
  ),
  ohlcv: z.array(
    z.object({
      ts: z.string(),
      open: num,
      high: num,
      low: num,
      close: num,
      volume_usd: num,
    }),
  ),
  counts: z.object({ observations: z.number(), candles: z.number() }),
  notes: z.array(z.string()),
});
export type TokenHistory = z.infer<typeof tokenHistorySchema>;

const variableSchema = z.object({
  name: z.string(),
  non_null_count: z.number(),
  missing_share: z.number(),
  availability: z.string(),
  description: z.string(),
  unit: z.string().nullable(),
  is_legal_model_input: z.boolean(),
});

export const researchSummarySchema = z.object({
  dataset: z.object({
    version_id: z.number(),
    label: z.string(),
    n_pools: z.number(),
    inactivity_threshold_hours: z.number(),
    criteria: z.record(z.string(), z.unknown()).nullable(),
  }),
  cohort_warnings: z.array(z.string()),
  variables: z.array(variableSchema),
  feature_availability: z.object({
    legal_model_inputs: z.array(z.string()),
    outcomes: z.array(z.string()),
    explanation: z.string(),
  }),
  cohort_breakdown_by_dex: z.object({
    column: z.string(),
    available: z.boolean(),
    groups: z.array(
      z.object({
        group: z.string(),
        n: z.number(),
        median_duration_hours: num,
        median_initial_liquidity_usd: num,
        event_rate: num,
      }),
    ),
  }),
  cohort_breakdown_by_discovery: z.object({
    column: z.string(),
    available: z.boolean(),
    groups: z.array(
      z.object({
        group: z.string(),
        n: z.number(),
        median_duration_hours: num,
        median_initial_liquidity_usd: num,
        event_rate: num,
      }),
    ),
  }),
  disclaimer: z.string(),
});
export type ResearchSummary = z.infer<typeof researchSummarySchema>;

export const describeSchema = z.object({
  column: z.string(),
  n: z.number(),
  missing_share: z.number(),
  available: z.boolean(),
  message: z.string().optional(),
  mean: num.optional(),
  std: num.optional(),
  min: num.optional(),
  max: num.optional(),
  p01: num.optional(),
  p05: num.optional(),
  q1: num.optional(),
  median: num.optional(),
  q3: num.optional(),
  p95: num.optional(),
  p99: num.optional(),
  iqr: num.optional(),
  skewness: num.optional(),
  kurtosis: num.optional(),
  zero_count: z.number().optional(),
  negative_count: z.number().optional(),
  notes: z.array(z.string()).optional(),
  cohort_warnings: z.array(z.string()).optional(),
});
export type Describe = z.infer<typeof describeSchema>;

export const histogramSchema = z.object({
  column: z.string(),
  n: z.number(),
  available: z.boolean(),
  missing_share: z.number(),
  message: z.string().optional(),
  scale: z.string().optional(),
  bin_edges: z.array(z.number()).optional(),
  counts: z.array(z.number()).optional(),
  excluded_nonpositive: z.number().optional(),
  note: z.string().optional(),
  cohort_warnings: z.array(z.string()).optional(),
});
export type Histogram = z.infer<typeof histogramSchema>;

export const statResultSchema = z.object({
  method: z.string(),
  statistic_name: z.string(),
  statistic: num,
  n: z.number(),
  missing_share: z.number(),
  p_value: num,
  ci_lower: num,
  ci_upper: num,
  effect_size: num,
  effect_size_name: z.string().nullable(),
  warnings: z.array(z.string()),
  details: z.record(z.string(), z.unknown()),
  dataset_version_id: z.number().optional(),
});
export type StatResult = z.infer<typeof statResultSchema>;

export const correlationMatrixSchema = z.object({
  method: z.string(),
  columns: z.array(z.string()),
  matrix: z.array(z.array(z.number().nullable())),
  pairwise_n: z.array(z.array(z.number())),
  pairs: z.array(
    z.object({
      x: z.string(),
      y: z.string(),
      coefficient: z.number(),
      n: z.number(),
      p_value: z.number(),
      p_value_adjusted: num,
      significant_after_fdr: z.boolean(),
      interpretation: z.string(),
    }),
  ),
  warnings: z.array(z.string()),
  dataset_version_id: z.number().optional(),
  n_pools: z.number().optional(),
});
export type CorrelationMatrix = z.infer<typeof correlationMatrixSchema>;

export const survivalSchema = z.object({
  available: z.boolean(),
  n: z.number().optional(),
  events_observed: z.number().optional(),
  censored: z.number().optional(),
  censored_share: num.optional(),
  missing_share: num.optional(),
  method: z.string().optional(),
  event_definition: z.string().optional(),
  time_unit: z.string().optional(),
  message: z.string().optional(),
  group_column: z.string().optional(),
  curves: z
    .array(
      z.object({
        label: z.string(),
        n: z.number(),
        events: z.number(),
        times: z.array(z.number()),
        survival: z.array(z.number()),
        ci_lower: z.array(z.number()),
        ci_upper: z.array(z.number()),
        median_survival_hours: num,
        median_note: z.string(),
      }),
    )
    .optional(),
  logrank: z
    .object({
      test: z.string().optional(),
      statistic: z.number().optional(),
      p_value: z.number().optional(),
      interpretation: z.string().optional(),
      error: z.string().optional(),
    })
    .optional(),
  warnings: z.array(z.string()),
  dataset_version_id: z.number().optional(),
  dataset_label: z.string().optional(),
});
export type Survival = z.infer<typeof survivalSchema>;

export const coxSchema = z.object({
  available: z.boolean(),
  method: z.string().optional(),
  message: z.string().optional(),
  error: z.string().optional(),
  n: z.number().optional(),
  events_observed: z.number().optional(),
  censored: z.number().optional(),
  events_per_covariate: num.optional(),
  concordance_index: num.optional(),
  concordance_note: z.string().optional(),
  covariates: z.array(z.string()).optional(),
  log_transformed: z.array(z.string()).optional(),
  coefficients: z
    .array(
      z.object({
        covariate: z.string(),
        coefficient: z.number(),
        hazard_ratio: z.number(),
        se: z.number(),
        ci_lower_hr: z.number(),
        ci_upper_hr: z.number(),
        p_value: z.number(),
        interpretation: z.string(),
      }),
    )
    .optional(),
  proportional_hazards_test: z.record(z.string(), z.unknown()).optional(),
  legal_model_inputs: z.array(z.string()).optional(),
  warnings: z.array(z.string()).optional(),
});
export type Cox = z.infer<typeof coxSchema>;

export const sensitivitySchema = z.object({
  available: z.boolean().optional(),
  research_question: z.string().optional(),
  message: z.string().optional(),
  rows: z
    .array(
      z.object({
        threshold_hours: z.number(),
        n: z.number(),
        available: z.boolean(),
        events_observed: z.number().optional(),
        event_rate: num.optional(),
        median_duration_observed: num.optional(),
      }),
    )
    .optional(),
  versions_compared: z
    .array(
      z.object({
        id: z.number(),
        label: z.string(),
        inactivity_threshold_hours: z.number(),
        pool_count: z.number(),
      }),
    )
    .optional(),
  warnings: z.array(z.string()).optional(),
});
export type Sensitivity = z.infer<typeof sensitivitySchema>;

export const datasetListSchema = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      label: z.string(),
      description: z.string().nullable(),
      pool_count: z.number(),
      observation_count: z.number(),
      candle_count: z.number(),
      inception_cohort_share: num,
      inactivity_threshold_hours: z.number(),
      criteria: z.record(z.string(), z.unknown()).nullable(),
      built_at: z.string().nullable(),
    }),
  ),
  count: z.number(),
  note: z.string(),
});
export type DatasetList = z.infer<typeof datasetListSchema>;

const experimentResultSchema = z.object({
  metric_name: z.string(),
  metric_value: num,
  metric_text: z.string().nullable(),
  sample_size: int,
  ci_lower: num,
  ci_upper: num,
  p_value: num,
  split: z.string().nullable(),
  details: z.record(z.string(), z.unknown()).nullable(),
});

export const experimentListSchema = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      name: z.string(),
      research_question: z.string(),
      method: z.string(),
      dataset_version_id: z.number().nullable(),
      created_at: z.string().nullable(),
      result_count: z.number(),
    }),
  ),
  pagination: z.object({
    page: z.number(),
    page_size: z.number(),
    total: z.number(),
    total_pages: z.number(),
  }),
});
export type ExperimentList = z.infer<typeof experimentListSchema>;

export const experimentDetailSchema = z.object({
  id: z.number(),
  name: z.string(),
  research_question: z.string(),
  hypothesis: z.string().nullable(),
  method: z.string(),
  features_used: z.array(z.string()).nullable(),
  target_definition: z.string().nullable(),
  split_strategy: z.string().nullable(),
  parameters: z.record(z.string(), z.unknown()).nullable(),
  notes: z.string().nullable(),
  limitations: z.string().nullable(),
  created_at: z.string().nullable(),
  dataset_version: z
    .object({
      id: z.number(),
      label: z.string(),
      pool_count: z.number(),
      inactivity_threshold_hours: z.number(),
      inception_cohort_share: num,
      criteria: z.record(z.string(), z.unknown()).nullable(),
    })
    .nullable(),
  results: z.array(experimentResultSchema),
  reproducibility: z.object({
    dataset_version_id: z.number().nullable(),
    note: z.string(),
  }),
});
export type ExperimentDetail = z.infer<typeof experimentDetailSchema>;

export const dataQualitySchema = z.union([
  z.object({
    dataset_empty: z.literal(true),
    message: z.string(),
    checks: z.array(z.unknown()),
  }),
  z.object({
    dataset_empty: z.literal(false),
    overall_status: z.string(),
    counts: z.object({
      pools: z.number(),
      observations: z.number(),
      candles: z.number(),
    }),
    checks: z.array(
      z.object({
        name: z.string(),
        status: z.string(),
        detail: z.unknown(),
        message: z.string(),
      }),
    ),
    jobs: z.object({
      by_status: z.record(z.string(), z.number()),
      last_successful_at: z.string().nullable(),
      total_rate_limit_hits: z.number(),
      recent_failures: z.array(
        z.object({
          id: z.number(),
          job_type: z.string(),
          status: z.string(),
          started_at: z.string().nullable(),
          error_message: z.string().nullable(),
        }),
      ),
    }),
    sources: z.array(
      z.object({
        name: z.string(),
        enabled: z.boolean(),
        requires_api_key: z.boolean(),
        observations_contributed: z.number(),
        description: z.string().nullable(),
      }),
    ),
    database: z.object({
      engine: z.string(),
      foreign_keys_enforced: z.boolean(),
    }),
    generated_at: z.string(),
  }),
]);
export type DataQuality = z.infer<typeof dataQualitySchema>;

export const jobListSchema = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      job_type: z.string(),
      status: z.string(),
      params: z.record(z.string(), z.unknown()).nullable(),
      started_at: z.string().nullable(),
      finished_at: z.string().nullable(),
      duration_seconds: num,
      records_written: z.number(),
      records_skipped: z.number(),
      http_requests_made: z.number(),
      rate_limit_hits: z.number(),
      error_message: z.string().nullable(),
      notes: z.string().nullable(),
    }),
  ),
  count: z.number(),
});
export type JobList = z.infer<typeof jobListSchema>;

export const rateLimitSchema = z.object({
  configured_requests_per_minute: z.record(z.string(), z.number()),
  documented_limits: z.string(),
  explanation: z.string(),
  behaviour_on_429: z.string(),
});
export type RateLimits = z.infer<typeof rateLimitSchema>;

export const dexListSchema = z.object({
  dexes: z.array(z.object({ dex: z.string(), pool_count: z.number() })),
});
export type DexList = z.infer<typeof dexListSchema>;

export const modelResultSchema = z.object({
  available: z.boolean(),
  error: z.string().optional(),
  message: z.string().optional(),
  target: z.string().optional(),
  target_definition: z.string().optional(),
  n: z.number().optional(),
  n_excluded_undetermined: z.number().optional(),
  class_counts: z.record(z.string(), z.number()).optional(),
  features_requested: z.array(z.string()).optional(),
  features_used: z.array(z.string()).optional(),
  preprocessing_notes: z.array(z.string()).optional(),
  split: z.record(z.string(), z.unknown()).optional(),
  random_seed: z.number().optional(),
  legal_model_inputs: z.array(z.string()).optional(),
  models: z
    .array(
      z.object({
        model: z.string(),
        is_baseline: z.boolean(),
        n_test: z.number(),
        accuracy: num,
        balanced_accuracy: num,
        roc_auc: num,
        average_precision: num,
        brier_score: num,
        confusion_matrix: z.record(z.string(), int),
        metric_notes: z.array(z.string()),
        coefficients: z
          .array(
            z.object({
              feature: z.string(),
              coefficient: z.number(),
              odds_ratio: z.number(),
              note: z.string(),
            }),
          )
          .optional(),
        feature_importances: z
          .array(z.object({ feature: z.string(), importance: z.number() }))
          .optional(),
        importance_caveat: z.string().optional(),
      }),
    )
    .optional(),
  warnings: z.array(z.string()).optional(),
  reproducibility: z.record(z.string(), z.unknown()).optional(),
});
export type ModelResult = z.infer<typeof modelResultSchema>;

/* ------------------------------------------------------------------------- */
/* Typed endpoint helpers                                                     */
/* ------------------------------------------------------------------------- */

export const api = {
  health: () => apiFetch("/api/health", healthSchema),
  overview: () => apiFetch("/api/overview", overviewSchema),
  tokens: (params: Record<string, QueryValue>) =>
    apiFetch("/api/tokens", tokenListSchema, { params }),
  dexes: () => apiFetch("/api/tokens/dexes", dexListSchema),
  tokenDetail: (identifier: string) =>
    apiFetch(`/api/tokens/${encodeURIComponent(identifier)}`, tokenDetailSchema),
  tokenHistory: (identifier: string, params?: Record<string, QueryValue>) =>
    apiFetch(
      `/api/tokens/${encodeURIComponent(identifier)}/history`,
      tokenHistorySchema,
      { params },
    ),
  researchSummary: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/summary", researchSummarySchema, { params }),
  describe: (column: string, params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/describe", describeSchema, {
      params: { column, ...params },
    }),
  histogram: (column: string, params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/histogram", histogramSchema, {
      params: { column, ...params },
    }),
  correlation: (x: string, y: string, params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/correlation", statResultSchema, {
      params: { x, y, ...params },
    }),
  correlationMatrix: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/correlation/matrix", correlationMatrixSchema, { params }),
  compareGroups: (params: Record<string, QueryValue>) =>
    apiFetch("/api/research/compare-groups", statResultSchema, { params }),
  survival: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/survival", survivalSchema, { params }),
  cox: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/survival/cox", coxSchema, { params }),
  sensitivity: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/research/survival/sensitivity", sensitivitySchema, { params }),
  datasets: () => apiFetch("/api/datasets", datasetListSchema),
  experiments: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/experiments", experimentListSchema, { params }),
  experiment: (id: string | number) =>
    apiFetch(`/api/experiments/${id}`, experimentDetailSchema),
  dataQuality: () => apiFetch("/api/data-quality", dataQualitySchema),
  jobs: (params?: Record<string, QueryValue>) =>
    apiFetch("/api/ingestion/jobs", jobListSchema, { params }),
  rateLimits: () => apiFetch("/api/ingestion/rate-limits", rateLimitSchema),
  fitModel: (body: Record<string, unknown>) =>
    apiFetch("/api/research/model", modelResultSchema, { method: "POST", body }),
};
