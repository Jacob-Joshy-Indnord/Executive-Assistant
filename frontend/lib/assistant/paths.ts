/**
 * Links between the pages follow the base path that is passed in, so that Flow can show
 * the same pages at the desk's own address.
 */
export type AssistantPaths = {
  assistant: string;
  settings: string;
  requests: string;
  organisation: string;
};

export function joinPath(basePath: string, page: string): string {
  const base = `/${basePath.replace(/^\/+|\/+$/g, "")}`.replace(/^\/$/, "");
  const rest = page.replace(/^\/+|\/+$/g, "");
  return rest ? `${base}/${rest}` : base || "/";
}

export function assistantPaths(basePath: string): AssistantPaths {
  return {
    assistant: joinPath(basePath, ""),
    settings: joinPath(basePath, "settings"),
    requests: joinPath(basePath, "requests"),
    organisation: joinPath(basePath, "organisation"),
  };
}
