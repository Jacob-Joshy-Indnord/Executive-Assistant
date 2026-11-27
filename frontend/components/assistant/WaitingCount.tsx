"use client";

/**
 * For Flow's top bar: the number of things waiting for the manager (requests to approve
 * and things that need their attention). It refreshes quietly like the pages, shows
 * nothing for someone who has no assistant, and links to the desk when given an address.
 */

import { useCallback } from "react";
import type { AssistantClient } from "@/lib/assistant/client";
import { useResource } from "@/lib/assistant/refresh";
import type { WaitingCount as WaitingCountView } from "@/lib/assistant/types";
import { Badge } from "./parts";

/** The number on its own, for a top bar that draws its own badge. */
export async function waitingCount(client: AssistantClient): Promise<number> {
  try {
    return (await client.waitingCount()).count;
  } catch {
    return 0;
  }
}

export function useWaitingCount(client: AssistantClient): WaitingCountView | null {
  const load = useCallback(() => client.waitingCount(), [client]);
  const { data } = useResource(load);
  return data ?? null;
}

function words(count: number): string {
  return count === 1 ? "1 thing waits for you" : `${count} things wait for you`;
}

export function WaitingCount({ client, href }: { client: AssistantClient; href?: string }) {
  const waiting = useWaitingCount(client);
  if (!waiting || waiting.count === 0) return null;
  const badge = (
    <Badge variant="accent" dot>
      <span aria-hidden="true">{waiting.count}</span>
      <span className="sr-only">{words(waiting.count)}</span>
    </Badge>
  );
  if (!href) return <span title={words(waiting.count)}>{badge}</span>;
  return (
    <a
      href={href}
      title={words(waiting.count)}
      className="inline-flex rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent"
    >
      {badge}
    </a>
  );
}
