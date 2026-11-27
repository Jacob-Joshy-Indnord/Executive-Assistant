/** Story 6.1: one API client, made from a base address and a fetch function passed in. */
import { describe, expect, it, vi } from "vitest";
import { ApiError, UNREACHABLE, createAssistantClient, messageOf } from "@/lib/assistant/client";

function answer(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("createAssistantClient", () => {
  it("calls the passed-in fetch at the passed-in base address", async () => {
    const fetch = vi.fn(() => answer(200, { count: 0, requests: [] }));
    const client = createAssistantClient({ baseUrl: "https://flow.example/desk/api/assistant/", fetch });
    await client.requests();
    expect(fetch).toHaveBeenCalledWith(
      "https://flow.example/desk/api/assistant/requests",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("sends JSON bodies and encodes ids", async () => {
    const fetch = vi.fn(() => answer(200, {}));
    const client = createAssistantClient({ baseUrl: "/api/assistant", fetch });
    await client.approve("a/b", "Edited words");
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/assistant/requests/a%2Fb/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ text: "Edited words" });
  });

  it("turns a failure into its one plain sentence", async () => {
    const fetch = vi.fn(() => answer(409, { detail: { code: "conflict", message: "This has already changed." } }));
    const client = createAssistantClient({ baseUrl: "/api/assistant", fetch });
    const failure = await client.decline("1").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(messageOf(failure)).toBe("This has already changed.");
    expect((failure as ApiError).status).toBe(409);
  });

  it("never shows technical details", async () => {
    const validation = vi.fn(() => answer(422, { detail: [{ loc: ["body", "text"], msg: "field required" }] }));
    const client = createAssistantClient({ baseUrl: "/api/assistant", fetch: validation });
    expect(messageOf(await client.sendChat("").catch((e: unknown) => e))).toBe("Check what you entered and try again.");
    const down = createAssistantClient({ baseUrl: "/x", fetch: () => Promise.reject(new TypeError("Failed to fetch")) });
    expect(messageOf(await down.chat().catch((e: unknown) => e))).toBe(UNREACHABLE);
  });
});
