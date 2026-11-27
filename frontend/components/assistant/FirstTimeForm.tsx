"use client";

import { useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import type { EffectiveSettings } from "@/lib/assistant/types";
import { Btn, ErrorLine, FormActions, Panel, SecHead } from "./parts";
import { PreferencesFields, draftFrom, formFrom } from "./PreferencesFields";

/** Shown while the assistant is new: every part is optional; saving or skipping starts it. */
export function FirstTimeForm({ defaults, onDone }: { defaults: EffectiveSettings | null; onDone: () => void }) {
  const { client } = useAssistant();
  const [draft, setDraft] = useState(() => draftFrom(null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDone();
    } catch (failure) {
      setError(messageOf(failure));
      setBusy(false);
    }
  };

  return (
    <Panel as="section" aria-label="How you work">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void run(() => client.savePreferences(formFrom(draft)));
        }}
      >
      <SecHead
        level={2}
        title="How you work"
        description="Answer what you like. What you leave empty keeps the usual setting."
      />
      <PreferencesFields draft={draft} onChange={setDraft} defaults={defaults} />
      {error && <ErrorLine>{error}</ErrorLine>}
      <FormActions>
        <Btn variant="primary" type="submit" disabled={busy}>
          Save
        </Btn>
        <Btn variant="ghost" disabled={busy} onClick={() => void run(() => client.skipPreferences())}>
          Skip
        </Btn>
      </FormActions>
      </form>
    </Panel>
  );
}
