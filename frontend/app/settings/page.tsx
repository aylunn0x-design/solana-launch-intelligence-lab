/**
 * Settings.
 *
 * Read-only by design. The platform's behaviour is configured by environment
 * variables on the backend, so a UI that let you edit them here would either be
 * fake or would need a privileged write endpoint this project has no use for.
 * This page shows what the running backend is actually configured with.
 */

import { api, type Health, type RateLimits } from "@/lib/api";
import { API_BASE_URL } from "@/lib/api";
import { ApiErrorPanel } from "@/components/ui/api-error";
import {
  Badge,
  Callout,
  KeyValue,
  PageHeader,
  Panel,
  SectionNote,
  StatusPill,
} from "@/components/ui/primitives";
import { DataTable } from "@/components/charts/chrome";
import { decimal } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Settings" };

export default async function SettingsPage() {
  let health: Health;
  let limits: RateLimits | null = null;
  try {
    health = await api.health();
    limits = await api.rateLimits().catch(() => null);
  } catch (error) {
    return (
      <>
        <PageHeader title="Settings" />
        <ApiErrorPanel error={error} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="The running backend's configuration. Read-only: behaviour is set through environment variables, not through this UI."
        actions={<StatusPill status={health.status} />}
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Connection">
          <dl>
            <KeyValue label="API base URL">
              <span className="tnum break-all">{API_BASE_URL}</span>
            </KeyValue>
            <KeyValue label="Backend status">
              <StatusPill status={health.status} />
            </KeyValue>
            <KeyValue label="Environment">{health.environment}</KeyValue>
            <KeyValue label="Database engine">{health.database.engine}</KeyValue>
            <KeyValue label="Database reachable">
              {health.database.reachable ? "yes" : "no"}
            </KeyValue>
            {health.database.error ? (
              <KeyValue label="Database error">
                <span className="text-status-critical">{health.database.error}</span>
              </KeyValue>
            ) : null}
          </dl>
          <SectionNote>
            Change the API URL with the <code className="tnum">NEXT_PUBLIC_API_BASE_URL</code>{" "}
            environment variable in the frontend.
          </SectionNote>
        </Panel>

        <Panel title="Data sources">
          <DataTable
            columns={["Source", "Enabled", "API key"]}
            rows={health.data_sources.map((source) => [
              source.name,
              source.enabled ? "yes" : "no",
              source.requires_api_key ? "required" : "not required",
            ])}
          />
          <div className="mt-3">
            <dl>
              <KeyValue
                label="Holder collection"
                hint="Collecting holder-concentration metrics from a Solana RPC node."
              >
                {health.holder_collection_enabled ? (
                  <Badge tone="good">enabled</Badge>
                ) : (
                  <Badge tone="warning">disabled</Badge>
                )}
              </KeyValue>
            </dl>
          </div>
          {!health.holder_collection_enabled ? (
            <div className="mt-3">
              <Callout severity="medium" title="Why holder collection is off">
                The public Solana RPC rate-limits{" "}
                <code className="tnum">getTokenLargestAccounts</code> and caps it at
                20 accounts. Enabling it meaningfully requires a dedicated RPC
                provider: set <code className="tnum">SOLANA_RPC_URL</code> and{" "}
                <code className="tnum">ENABLE_HOLDER_COLLECTION=true</code> on the
                backend. That provider will need an API key, which is a cost
                decision for the project owner.
              </Callout>
            </div>
          ) : null}
        </Panel>

        {limits ? (
          <Panel title="Rate limits" className="lg:col-span-2">
            <dl>
              {Object.entries(limits.configured_requests_per_minute).map(([name, value]) => (
                <KeyValue key={name} label={name}>
                  <span className="tnum">{decimal(value, 0)} requests / minute</span>
                </KeyValue>
              ))}
              <KeyValue label="Documented provider limits">
                <Badge tone="warning">{limits.documented_limits}</Badge>
              </KeyValue>
            </dl>
            <SectionNote>{limits.explanation}</SectionNote>
            <SectionNote>{limits.behaviour_on_429}</SectionNote>
          </Panel>
        ) : null}

        <Panel title="Configuration reference" className="lg:col-span-2">
          <p className="mb-3 text-[13px] leading-relaxed text-ink-secondary">
            Backend environment variables, with their defaults. No secrets belong in
            source control; see <code className="tnum">.env.example</code>.
          </p>
          <DataTable
            columns={["Variable", "Default", "Purpose"]}
            rows={[
              ["DATABASE_URL", "sqlite:///./solana_lab.db", "SQLAlchemy URL; use postgresql+psycopg:// for Postgres"],
              ["ENVIRONMENT", "development", "Controls docs exposure and CORS"],
              ["CORS_ORIGINS", "http://localhost:3000", "Comma-separated allowed frontend origins"],
              ["GECKOTERMINAL_RATE_LIMIT_PER_MIN", "25", "Primary source request budget"],
              ["DEXSCREENER_RATE_LIMIT_PER_MIN", "50", "Secondary source request budget"],
              ["SOLANA_RPC_RATE_LIMIT_PER_MIN", "20", "RPC request budget"],
              ["ENABLE_HOLDER_COLLECTION", "false", "Holder metrics; needs a dedicated RPC provider"],
              ["SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com", "RPC endpoint"],
              ["DEFAULT_INACTIVITY_THRESHOLD_HOURS", "24", "Default survival definition"],
              ["MIN_CANDLES_FOR_ANALYSIS", "6", "Minimum observations to enter a dataset"],
              ["HTTP_TIMEOUT_SECONDS", "25", "Per-request timeout"],
              ["HTTP_MAX_RETRIES", "4", "Retry budget on 429/5xx"],
            ]}
          />
        </Panel>

        <Panel title="Disclaimer" className="lg:col-span-2">
          <Callout severity="info" title="Scope of this software">
            {health.disclaimer}
          </Callout>
        </Panel>
      </div>
    </>
  );
}
