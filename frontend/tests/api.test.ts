/**
 * API client tests.
 *
 * The behaviour under test is the project's central rule: a failed or
 * malformed response must raise, so the UI shows an error state. It must never
 * fall back to sample data.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { ApiError, SchemaError, apiFetch } from "@/lib/api";

const schema = z.object({ value: z.number(), label: z.string() });

function mockFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", impl);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("returns validated data on success", async () => {
    mockFetch(async () => new Response(JSON.stringify({ value: 42, label: "ok" })));
    const result = await apiFetch("/api/thing", schema);
    expect(result).toEqual({ value: 42, label: "ok" });
  });

  it("throws ApiError with the server's detail on an HTTP error", async () => {
    mockFetch(
      async () =>
        new Response(JSON.stringify({ detail: "No dataset version found." }), {
          status: 404,
        }),
    );
    await expect(apiFetch("/api/thing", schema)).rejects.toThrowError(ApiError);
    await expect(apiFetch("/api/thing", schema)).rejects.toMatchObject({
      status: 404,
      detail: "No dataset version found.",
    });
  });

  it("marks a network failure as unreachable rather than an HTTP error", async () => {
    mockFetch(async () => {
      throw new TypeError("fetch failed");
    });
    try {
      await apiFetch("/api/thing", schema);
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isUnreachable).toBe(true);
      expect((error as ApiError).detail).toContain("uvicorn");
    }
  });

  it("throws SchemaError when the response shape is wrong", async () => {
    mockFetch(async () => new Response(JSON.stringify({ value: "not a number" })));
    await expect(apiFetch("/api/thing", schema)).rejects.toThrowError(SchemaError);
  });

  it("reports which fields failed validation", async () => {
    mockFetch(async () => new Response(JSON.stringify({ value: "nope", label: 1 })));
    try {
      await apiFetch("/api/thing", schema);
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(SchemaError);
      expect((error as SchemaError).issues.join(" ")).toContain("value");
    }
  });

  it("never substitutes sample data for a failure", async () => {
    mockFetch(async () => new Response("upstream exploded", { status: 500 }));
    // The contract: it throws. A silent fallback would put invented numbers on
    // screen, which is the one thing this platform must not do.
    await expect(apiFetch("/api/thing", schema)).rejects.toThrowError(ApiError);
  });

  it("builds query strings, skipping empty values", async () => {
    let seen = "";
    mockFetch(async (input) => {
      seen = String(input);
      return new Response(JSON.stringify({ value: 1, label: "x" }));
    });
    await apiFetch("/api/thing", schema, {
      params: { a: "1", b: undefined, c: null, d: "", e: 5 },
    });
    expect(seen).toContain("a=1");
    expect(seen).toContain("e=5");
    expect(seen).not.toContain("b=");
    expect(seen).not.toContain("c=");
    expect(seen).not.toContain("d=");
  });

  it("repeats array parameters", async () => {
    let seen = "";
    mockFetch(async (input) => {
      seen = String(input);
      return new Response(JSON.stringify({ value: 1, label: "x" }));
    });
    await apiFetch("/api/thing", schema, { params: { ids: ["1", "2"] } });
    expect(seen).toContain("ids=1");
    expect(seen).toContain("ids=2");
  });
});
