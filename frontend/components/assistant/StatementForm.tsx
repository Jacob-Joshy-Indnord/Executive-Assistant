"use client";

/**
 * Giving, confirming or correcting a status. The owner always chooses the colour; each
 * colour is named in words with what it means in the organisation beside it.
 */

import { useId, useState } from "react";
import type { Colour, ColourMeaning, StatementAnswer, Wording } from "@/lib/assistant/types";
import { Btn, ChoiceGroup, ColourName, ErrorLine, Field, FormActions, Input, Textarea } from "./parts";

export type StatementFormProps = {
  colours: ColourMeaning[];
  colour: Colour | null;
  wording: Wording | null;
  /** The button's name, given whether the words were changed from what was offered. */
  submitLabel: (changed: boolean) => string;
  onSubmit: (answer: StatementAnswer) => Promise<string | null>;
  onCancel?: () => void;
};

export function StatementForm({ colours, colour: startColour, wording, submitLabel, onSubmit, onCancel }: StatementFormProps) {
  const name = useId();
  const ids = { reason: useId(), changed: useId(), next: useId(), error: useId() };
  const [colour, setColour] = useState<Colour | null>(startColour);
  const [reason, setReason] = useState(wording?.reason ?? "");
  const [whatChanged, setWhatChanged] = useState(wording?.what_changed ?? "");
  const [whatNext, setWhatNext] = useState(wording?.what_comes_next ?? "");
  const [colourError, setColourError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const changed =
    colour !== startColour ||
    reason !== (wording?.reason ?? "") ||
    whatChanged !== (wording?.what_changed ?? "") ||
    whatNext !== (wording?.what_comes_next ?? "");

  const submit = async () => {
    setError(null);
    setColourError(null);
    if (!colour) return setColourError("Choose a colour.");
    if (!reason.trim()) return setError("Give a reason.");
    setBusy(true);
    const problem = await onSubmit({ colour, reason, what_changed: whatChanged, what_comes_next: whatNext });
    setBusy(false);
    if (problem) setError(problem);
  };

  const meaning = (c: Colour) => colours.find((m) => m.colour === c)?.meaning;

  return (
    <form
      className="flex flex-col gap-3 rounded-panel bg-brand-accent-soft/50 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <ChoiceGroup<Colour>
        legend="Colour"
        name={name}
        value={colour}
        onChange={setColour}
        error={colourError}
        options={(["green", "amber", "red"] as const).map((c) => ({
          value: c,
          label: <ColourName colour={c} />,
          description: meaning(c) ?? "No meaning written yet.",
        }))}
      />
      <Field label="Reason" htmlFor={ids.reason}>
        <Textarea id={ids.reason} rows={2} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} required />
      </Field>
      <Field label="What changed" htmlFor={ids.changed}>
        <Input id={ids.changed} value={whatChanged} maxLength={300} onChange={(e) => setWhatChanged(e.target.value)} />
      </Field>
      <Field label="What comes next" htmlFor={ids.next}>
        <Input id={ids.next} value={whatNext} maxLength={300} onChange={(e) => setWhatNext(e.target.value)} />
      </Field>
      {error && <ErrorLine id={ids.error}>{error}</ErrorLine>}
      <FormActions>
        <Btn size="sm" variant="primary" type="submit" disabled={busy}>
          {submitLabel(changed)}
        </Btn>
        {onCancel && (
          <Btn size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Btn>
        )}
      </FormActions>
    </form>
  );
}
