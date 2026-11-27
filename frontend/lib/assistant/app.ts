"use client";

/**
 * The client the standalone pages use: the module's addresses under `/api/assistant` on
 * this site, called with the browser's own fetch. Flow passes its own client instead.
 */

import { createAssistantClient, type AssistantClient } from "./client";

export const APP_BASE_URL = "/api/assistant";
export const APP_BASE_PATH = "/assistant";

let shared: AssistantClient | null = null;

export function appClient(): AssistantClient {
  shared ??= createAssistantClient({
    baseUrl: APP_BASE_URL,
    fetch: (input, init) => window.fetch(input, { ...init, credentials: "same-origin" }),
  });
  return shared;
}
