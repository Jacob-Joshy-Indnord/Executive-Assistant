"use client";

/** The API client and the page links, handed down from the page's component. */

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { AssistantClient } from "./client";
import { assistantPaths, type AssistantPaths } from "./paths";

type AssistantContextValue = { client: AssistantClient; paths: AssistantPaths };

const AssistantContext = createContext<AssistantContextValue | null>(null);

export function AssistantProvider({
  client,
  basePath,
  children,
}: {
  client: AssistantClient;
  basePath: string;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ client, paths: assistantPaths(basePath) }), [client, basePath]);
  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}

export function useAssistant(): AssistantContextValue {
  const value = useContext(AssistantContext);
  if (!value) throw new Error("useAssistant needs an AssistantProvider");
  return value;
}

/** What every page component is given, so that Flow can show it at its own address. */
export type PageProps = { client: AssistantClient; basePath: string };
