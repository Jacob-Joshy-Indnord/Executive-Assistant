/** A stand-in for the module's addresses: the real client over a fetch that answers from routes. */
import { createAssistantClient } from "@/lib/assistant/client";

export class Refuse {
  constructor(
    readonly status: number,
    readonly message: string,
  ) {}
}

type Answer = unknown | Refuse;
export type Route = Answer | ((body: unknown, url: URL) => Answer | Promise<Answer>);

export type Call = { method: string; path: string; body: unknown };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function fakeApi(routes: Record<string, Route>, baseUrl = "/api/assistant") {
  const calls: Call[] = [];
  const fetch = async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://module.test");
    const path = url.pathname.slice(baseUrl.length + 1);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const route = routes[`${method} ${path}`];
    if (route === undefined) return json(404, { detail: { code: "not_found", message: "Not found." } });
    const answer = typeof route === "function" ? await (route as (b: unknown, u: URL) => unknown)(body, url) : route;
    if (answer instanceof Refuse) return json(answer.status, { detail: { code: "refused", message: answer.message } });
    return json(200, answer);
  };
  return { client: createAssistantClient({ baseUrl, fetch }), calls, routes };
}

/** A promise the test settles when it chooses. */
export function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
