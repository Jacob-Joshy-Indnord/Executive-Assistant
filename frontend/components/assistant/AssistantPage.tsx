"use client";

/**
 * The Assistant page (section 13.1): the manager's private desk. Its parts come from the
 * section registry, in order, each with a one-line summary and a count.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Settings } from "lucide-react";
import { ApiError, messageOf } from "@/lib/assistant/client";
import { AssistantProvider, useAssistant, type PageProps } from "@/lib/assistant/context";
import { formatDayTime } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { AssistantView, PreferencesView } from "@/lib/assistant/types";
import { Btn, Empty, ErrorLine, Panel } from "./parts";
import { FirstTimeForm } from "./FirstTimeForm";
import { SECTIONS } from "./sections";
import type { SectionRegistry } from "./sections/registry";

export const NO_ASSISTANT = "You have no Executive Assistant yet.";

function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

/** The line shown while the assistant is paused, with Resume. */
function PausedLine({ assistant, onChanged }: { assistant: AssistantView; onChanged: (a: AssistantView) => void }) {
  const { client } = useAssistant();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const byManager = assistant.state === "paused";

  const resume = async () => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await client.resume());
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel as="div" className="flex flex-col gap-2 border-brand-gold/60 dark:border-brand-gold/60">
      <div role="status" className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-ui-md font-medium text-brand-ink">
          {byManager ? "Your assistant is paused." : "Your organisation has paused all virtual employees."}
        </p>
        {byManager && (
          <Btn variant="primary" size="sm" onClick={() => void resume()} disabled={busy}>
            Resume
          </Btn>
        )}
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
    </Panel>
  );
}

function Header({ assistantName }: { assistantName: string }) {
  const { client, paths } = useAssistant();
  const loadStatus = useCallback(() => client.status(), [client]);
  const { data } = useResource(loadStatus);
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3">
        <h1 className="text-ui-title text-brand-ink">{assistantName}</h1>
        {data?.next_report_at && (
          <p className="text-ui text-brand-ink-muted">Next report {formatDayTime(data.next_report_at)}</p>
        )}
      </div>
      <Btn href={paths.settings} size="sm">
        <Settings aria-hidden="true" className="h-3.5 w-3.5" />
        Settings
      </Btn>
    </header>
  );
}

function Desk({ sections }: { sections: SectionRegistry }) {
  const { client } = useAssistant();
  const [assistant, setAssistant] = useState<AssistantView | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<PreferencesView["settings"] | null>(null);
  const opened = useRef(false);

  const load = useCallback(async () => {
    try {
      // The first open keeps the browser's time zone; later reads only look.
      const next = opened.current ? await client.assistant() : await client.opened(browserTimeZone());
      opened.current = true;
      setAssistant(next);
      setMissing(false);
      setError(null);
      return next;
    } catch (failure) {
      if (failure instanceof ApiError && failure.notFound) setMissing(true);
      else if (!opened.current) setError(messageOf(failure));
      return null;
    }
  }, [client]);

  const assistantResource = useResource(load);

  useEffect(() => {
    if (assistant?.form_offered && !defaults) {
      client
        .preferences()
        .then((p) => setDefaults(p.settings))
        .catch(() => undefined);
    }
  }, [assistant?.form_offered, client, defaults]);

  if (missing) {
    return (
      <Panel>
        <Empty title={NO_ASSISTANT} />
      </Panel>
    );
  }
  if (error) {
    return (
      <Panel className="flex flex-col gap-2">
        <ErrorLine>{error}</ErrorLine>
        <div>
          <Btn size="sm" onClick={() => void assistantResource.reload()}>
            Try again
          </Btn>
        </div>
      </Panel>
    );
  }
  if (!assistant) {
    return <p className="text-ui text-brand-ink-muted">Opening your assistant.</p>;
  }

  return (
    <>
      <Header assistantName={assistant.display_name} />
      {assistant.form_offered ? (
        <FirstTimeForm defaults={defaults} onDone={() => void assistantResource.reload()} />
      ) : (
        <>
          {!assistant.running && <PausedLine assistant={assistant} onChanged={setAssistant} />}
          {sections.all().map(({ key, Component }) => (
            <Component key={key} />
          ))}
        </>
      )}
    </>
  );
}

export function AssistantPage({ client, basePath, sections = SECTIONS }: PageProps & { sections?: SectionRegistry }) {
  return (
    <AssistantProvider client={client} basePath={basePath}>
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-3 py-4 sm:px-5 sm:py-6">
        <Desk sections={sections} />
      </main>
    </AssistantProvider>
  );
}


