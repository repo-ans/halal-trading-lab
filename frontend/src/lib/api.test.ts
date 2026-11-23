import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./api";

// Bug report: Strategy Lab showed "API 422: [object Object]" because the old
// _errorFor template-literal'd a non-string `detail` straight into the message.
// These tests exercise the real error path (a mocked fetch response, not a
// hand-built Error) through apiFetch, the same function every other API call
// goes through.

function mockResponse(status: number, body: unknown): Response {
  return {
    ok: false,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API error handling never stringifies an object into the message", () => {
  it("a plain string detail is shown as-is", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse(404, { detail: "instrument not found" })));

    await expect(apiFetch("/x", "t")).rejects.toMatchObject({
      message: expect.stringContaining("instrument not found"),
    });
  });

  it("an all_instruments_blocked-shaped object detail never renders [object Object], and is carried on .detail", async () => {
    const detail = {
      id: 1, status: "failed", error: "all_instruments_blocked",
      blocked_instruments: { "2": "no_record" }, compliance_check_ids: {},
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse(422, { detail })));

    try {
      await apiFetch("/x", "t");
      expect.unreachable("apiFetch should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.message).not.toContain("[object Object]");
      expect(apiErr.status).toBe(422);
      expect(apiErr.detail).toEqual(detail);
    }
  });

  it("a FastAPI validation-error array detail surfaces its message text, not [object Object]", async () => {
    const detail = [{ loc: ["body", "range_start"], msg: "field required", type: "missing" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse(422, { detail })));

    await expect(apiFetch("/x", "t")).rejects.toMatchObject({
      message: expect.stringContaining("field required"),
    });
    try {
      await apiFetch("/x", "t");
    } catch (err) {
      expect((err as Error).message).not.toContain("[object Object]");
    }
  });

  it("an unexpected/unknown object shape still falls back safely, never [object Object]", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse(500, { detail: { weird: { nested: true } } })));

    try {
      await apiFetch("/x", "t");
      expect.unreachable("apiFetch should have thrown");
    } catch (err) {
      const message = (err as Error).message;
      expect(message).not.toContain("[object Object]");
      expect(message).toContain("500");
    }
  });

  it("no detail at all still produces a plain, safe message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse(401, {})));

    await expect(apiFetch("/x", "t")).rejects.toMatchObject({ message: "API 401" });
  });
});
