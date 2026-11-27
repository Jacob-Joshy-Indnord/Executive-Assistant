"use client";

/**
 * Organisation settings (Story 6.4), for administrators: what each status colour means.
 * Owners see these meanings beside the colours when they give or confirm a status.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { ApiError, messageOf } from "@/lib/assistant/client";
import { AssistantProvider, useAssistant, type PageProps } from "@/lib/assistant/context";
import { useResource } from "@/lib/assistant/refresh";
import type { Colour } from "@/lib/assistant/types";
import { Btn, ColourName, Empty, ErrorLine, FormActions, Panel, SecHead, Textarea } from "./parts";

const COLOURS: Colour[] = ["green", "amber", "red"];

function MeaningField({ colour, value, onChange }: { colour: Colour; value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-ui-sm font-medium">
        <ColourName colour={colour} /> <span className="text-brand-ink">means</span>
      </label>
      <Textarea id={id} rows={2} maxLength={300} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function Organisation() {
  const { client } = useAssistant();
  const load = useCallback(() => client.organisationSettings(), [client]);
  const { data, error, reload } = useResource(load, { refresh: false });
  const [meanings, setMeanings] = useState<Record<Colour, string> | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data && !meanings) {
      setMeanings({
        green: data.colour_meanings.green ?? "",
        amber: data.colour_meanings.amber ?? "",
        red: data.colour_meanings.red ?? "",
      });
    }
  }, [data, meanings]);

  if (error && !data) {
    return (
      <Panel>
        {error instanceof ApiError && error.notFound ? (
          <Empty title="Only administrators can change the organisation settings." />
        ) : (
          <ErrorLine>{messageOf(error)}</ErrorLine>
        )}
      </Panel>
    );
  }

  const save = async () => {
    if (!meanings) return;
    setBusy(true);
    setSaved(false);
    setSaveError(null);
    try {
      await client.changeColourMeanings(meanings);
      setSaved(true);
      await reload();
    } catch (failure) {
      setSaveError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel as="section" aria-labelledby="colours-heading">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <SecHead
          id="colours-heading"
          title="Status colours"
          description="What each colour means here. Owners see it when they give a status."
        />
        {meanings ? (
          COLOURS.map((colour) => (
            <MeaningField
              key={colour}
              colour={colour}
              value={meanings[colour]}
              onChange={(value) => setMeanings({ ...meanings, [colour]: value })}
            />
          ))
        ) : (
          <p className="text-ui text-brand-ink-muted">Reading the settings.</p>
        )}
        {saveError && <ErrorLine>{saveError}</ErrorLine>}
        <FormActions>
          <Btn variant="primary" type="submit" disabled={busy || !meanings}>
            Save
          </Btn>
          <span role="status" className="text-ui-sm text-brand-ink-muted">
            {saved ? "Saved." : ""}
          </span>
        </FormActions>
      </form>
    </Panel>
  );
}

export function OrganisationSettingsPage({ client, basePath }: PageProps) {
  return (
    <AssistantProvider client={client} basePath={basePath}>
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-3 py-4 sm:px-5 sm:py-6">
        <h1 className="text-ui-title text-brand-ink">Organisation settings</h1>
        <Organisation />
      </main>
    </AssistantProvider>
  );
}
