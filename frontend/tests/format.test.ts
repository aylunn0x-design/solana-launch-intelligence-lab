/**
 * Formatting tests.
 *
 * The central guarantee under test: a missing value renders as an em dash and
 * never as 0. Conflating the two would make an unobserved pool look like a pool
 * with no liquidity.
 */

import { describe, expect, it } from "vitest";

import {
  EM_DASH,
  count,
  decimal,
  duration,
  humanize,
  interval,
  pValue,
  percent,
  shortAddress,
  signedPercent,
  usd,
} from "@/lib/format";
import { formatForVariable, resolveFormatter } from "@/components/charts/formats";

describe("usd", () => {
  it("renders missing values as an em dash, never as zero", () => {
    expect(usd(null)).toBe(EM_DASH);
    expect(usd(undefined)).toBe(EM_DASH);
    expect(usd(Number.NaN)).toBe(EM_DASH);
  });

  it("distinguishes a real zero from missing", () => {
    expect(usd(0)).toBe("$0");
    expect(usd(0)).not.toBe(usd(null));
  });

  it("compacts large values", () => {
    expect(usd(17_076_464)).toBe("$17.08M");
    expect(usd(5_233)).toBe("$5.2K");
    expect(usd(1_250_000_000)).toBe("$1.25B");
  });

  it("keeps significant digits for sub-dollar token prices", () => {
    // 8.19e-8 must not render as "$0.00".
    expect(usd(0.000000081888)).toContain("8.19");
  });
});

describe("count", () => {
  it("separates thousands and handles missing", () => {
    expect(count(5233)).toBe("5,233");
    expect(count(null)).toBe(EM_DASH);
    expect(count(0)).toBe("0");
  });
});

describe("percent", () => {
  it("scales proportions and respects already-percent values", () => {
    expect(percent(0.532)).toBe("53.2%");
    expect(percent(53.2, { alreadyPercent: true })).toBe("53.2%");
    expect(percent(null)).toBe(EM_DASH);
  });
});

describe("signedPercent", () => {
  it("always shows the direction", () => {
    expect(signedPercent(3.67)).toBe("+3.67%");
    expect(signedPercent(-3.67)).toBe("-3.67%");
    expect(signedPercent(null)).toBe(EM_DASH);
  });
});

describe("duration", () => {
  it("picks a readable unit", () => {
    expect(duration(0.5)).toBe("30m");
    expect(duration(18)).toBe("18.0h");
    expect(duration(96)).toBe("4.0d");
    expect(duration(null)).toBe(EM_DASH);
  });
});

describe("pValue", () => {
  it("never prints a misleading 0.000", () => {
    expect(pValue(0.00001)).toBe("< 0.0001");
    expect(pValue(0.0023)).toBe("0.0023");
    expect(pValue(null)).toBe(EM_DASH);
  });
});

describe("interval", () => {
  it("says when an interval was not computed", () => {
    expect(interval(0.163, 0.627)).toBe("[0.163, 0.627]");
    expect(interval(null, 0.5)).toBe("not computed");
    expect(interval(null, null)).toBe("not computed");
  });
});

describe("shortAddress", () => {
  it("abbreviates long base58 addresses", () => {
    const mint = "8zknH3bQTduTBKMU61ZsFMVwQLtVqPsS5pBzbzZgpump";
    expect(shortAddress(mint)).toBe("8zkn…pump");
    expect(shortAddress(null)).toBe(EM_DASH);
  });

  it("leaves short values intact", () => {
    expect(shortAddress("abc")).toBe("abc");
  });
});

describe("humanize", () => {
  it("turns feature names into labels", () => {
    expect(humanize("initial_liquidity_usd")).toBe("Initial Liquidity (USD)");
    expect(humanize("duration_hours")).toBe("Duration (Hours)");
    expect(humanize("max_drawdown_pct")).toBe("Max Drawdown (%)");
  });
});

describe("decimal", () => {
  it("fixes precision and handles missing", () => {
    expect(decimal(0.42183, 4)).toBe("0.4218");
    expect(decimal(null)).toBe(EM_DASH);
  });
});

describe("chart format descriptors", () => {
  it("resolves a serializable name to a formatter", () => {
    expect(resolveFormatter("usd")(5000)).toBe("$5.0K");
    expect(resolveFormatter("count")(1234)).toBe("1,234");
    expect(resolveFormatter("percent")(0.5)).toBe("50.0%");
  });

  it("renders missing values as an em dash through every format", () => {
    for (const format of ["usd", "count", "decimal", "duration", "percent"] as const) {
      expect(resolveFormatter(format)(null)).toBe(EM_DASH);
    }
  });

  it("chooses a format from the variable name", () => {
    expect(formatForVariable("initial_liquidity_usd")).toBe("usd");
    expect(formatForVariable("duration_hours")).toBe("duration");
    expect(formatForVariable("max_drawdown_pct")).toBe("percentPoints");
    expect(formatForVariable("realized_volatility")).toBe("decimal");
  });
});
