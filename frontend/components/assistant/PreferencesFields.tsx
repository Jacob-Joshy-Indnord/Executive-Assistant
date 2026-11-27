"use client";

/**
 * The five optional parts of the first-time form (Story 2.1), also used on Settings.
 * A field left empty keeps the section 8.1 default, which is shown as its placeholder.
 */

import { useId } from "react";
import type { EffectiveSettings, PreferencesForm, ReportFrequency, Weekday } from "@/lib/assistant/types";
import { Block, Field, Input, Select, Textarea, Toggle } from "./parts";

export const WEEKDAYS: { day: Weekday; word: string }[] = [
  { day: "monday", word: "Monday" },
  { day: "tuesday", word: "Tuesday" },
  { day: "wednesday", word: "Wednesday" },
  { day: "thursday", word: "Thursday" },
  { day: "friday", word: "Friday" },
  { day: "saturday", word: "Saturday" },
  { day: "sunday", word: "Sunday" },
];

const FREQUENCIES: { value: ReportFrequency; word: string }[] = [
  { value: "weekly", word: "Weekly" },
  { value: "every_two_weeks", word: "Every two weeks" },
  { value: "monthly", word: "Monthly" },
  { value: "off", word: "Off" },
];

export type PreferencesDraft = {
  days: Weekday[] | null;
  starts: string;
  ends: string;
  time_zone: string;
  tone: string;
  sign_off: string;
  priorities: string;
  key_people: string;
  follow_ups: string;
  answer_within: string;
  late_after: string;
  notices: string;
  frequency: string;
  day: string;
  time: string;
  ask_before: string;
  includes: string;
  leaves_out: string;
};

const text = (value: string | null | undefined) => value ?? "";
const number = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));
const lines = (value: string[] | null | undefined) => (value ?? []).join("\n");

export function draftFrom(form: PreferencesForm | null | undefined): PreferencesDraft {
  return {
    days: form?.working_time?.days ?? null,
    starts: text(form?.working_time?.starts)?.slice(0, 5),
    ends: text(form?.working_time?.ends)?.slice(0, 5),
    time_zone: text(form?.working_time?.time_zone),
    tone: text(form?.tone?.tone),
    sign_off: text(form?.tone?.sign_off),
    priorities: lines(form?.priorities?.priorities),
    key_people: lines(form?.priorities?.key_people),
    follow_ups: number(form?.follow_ups?.follow_ups),
    answer_within: number(form?.follow_ups?.answer_within_working_days),
    late_after: number(form?.follow_ups?.late_after_working_days),
    notices: number(form?.follow_ups?.notices_per_owner_per_day),
    frequency: text(form?.report?.frequency),
    day: text(form?.report?.day),
    time: text(form?.report?.time)?.slice(0, 5),
    ask_before: number(form?.report?.ask_owners_working_days_before),
    includes: text(form?.report?.includes),
    leaves_out: text(form?.report?.leaves_out),
  };
}

const orNull = (value: string) => (value.trim() ? value.trim() : null);
const numberOrNull = (value: string) => (value === "" ? null : Number(value));
const listOrNull = (value: string) => {
  const entries = value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return entries.length ? entries : null;
};

/** Only what was answered is sent; a part with nothing in it is left out. */
export function formFrom(draft: PreferencesDraft): PreferencesForm {
  const part = <T extends Record<string, unknown>>(values: T): T | null =>
    Object.values(values).some((v) => v !== null) ? values : null;
  return {
    working_time: part({
      days: draft.days,
      starts: orNull(draft.starts),
      ends: orNull(draft.ends),
      time_zone: orNull(draft.time_zone),
    }),
    tone: part({ tone: orNull(draft.tone), sign_off: orNull(draft.sign_off) }),
    priorities: part({ priorities: listOrNull(draft.priorities), key_people: listOrNull(draft.key_people) }),
    follow_ups: part({
      follow_ups: numberOrNull(draft.follow_ups),
      answer_within_working_days: numberOrNull(draft.answer_within),
      late_after_working_days: numberOrNull(draft.late_after),
      notices_per_owner_per_day: numberOrNull(draft.notices),
    }),
    report: part({
      frequency: (orNull(draft.frequency) as ReportFrequency | null) ?? null,
      day: (orNull(draft.day) as Weekday | null) ?? null,
      time: orNull(draft.time),
      ask_owners_working_days_before: numberOrNull(draft.ask_before),
      includes: orNull(draft.includes),
      leaves_out: orNull(draft.leaves_out),
    }),
  };
}

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

function NumberSelect({
  id,
  value,
  onChange,
  from,
  to,
  defaultWord,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  from: number;
  to: number;
  defaultWord: string;
}) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{defaultWord}</option>
      {range(from, to).map((n) => (
        <option key={n} value={String(n)}>
          {n}
        </option>
      ))}
    </Select>
  );
}

export type PreferencesFieldsProps = {
  draft: PreferencesDraft;
  onChange: (draft: PreferencesDraft) => void;
  /** What applies now, shown where a field is left empty. */
  defaults: EffectiveSettings | null;
  /** Show only some parts, e.g. the report on its own. */
  parts?: ("working_time" | "tone" | "priorities" | "follow_ups" | "report")[];
};

export function PreferencesFields({
  draft,
  onChange,
  defaults,
  parts = ["working_time", "tone", "priorities", "follow_ups", "report"],
}: PreferencesFieldsProps) {
  const id = useId();
  const set = <K extends keyof PreferencesDraft>(key: K, value: PreferencesDraft[K]) => onChange({ ...draft, [key]: value });
  const days = draft.days ?? defaults?.working_days ?? ["monday", "tuesday", "wednesday", "thursday", "friday"];
  const dayWord = (day: string | undefined) => WEEKDAYS.find((w) => w.day === day)?.word ?? "";

  return (
    <div className="flex flex-col gap-5">
      {parts.includes("working_time") && (
        <Block label="Working days and hours">
          <fieldset className="flex min-w-0 flex-col gap-1">
            <legend className="mb-1 text-ui-sm font-medium text-brand-ink">Working days</legend>
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
              {WEEKDAYS.map(({ day, word }) => (
                <Toggle
                  key={day}
                  label={word}
                  checked={days.includes(day)}
                  onChange={(on) => {
                    const next = on ? [...days, day] : days.filter((d) => d !== day);
                    set("days", WEEKDAYS.map((w) => w.day).filter((d) => next.includes(d)));
                  }}
                />
              ))}
            </div>
          </fieldset>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Starts" htmlFor={`${id}-starts`}>
              <Input id={`${id}-starts`} type="time" value={draft.starts} placeholder={defaults?.starts} onChange={(e) => set("starts", e.target.value)} />
            </Field>
            <Field label="Ends" htmlFor={`${id}-ends`}>
              <Input id={`${id}-ends`} type="time" value={draft.ends} placeholder={defaults?.ends} onChange={(e) => set("ends", e.target.value)} />
            </Field>
            <Field label="Time zone" htmlFor={`${id}-zone`}>
              <Input id={`${id}-zone`} value={draft.time_zone} placeholder={defaults?.time_zone} onChange={(e) => set("time_zone", e.target.value)} />
            </Field>
          </div>
        </Block>
      )}

      {parts.includes("tone") && (
        <Block label="Tone and sign-off">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Tone" htmlFor={`${id}-tone`}>
              <Input id={`${id}-tone`} value={draft.tone} maxLength={300} onChange={(e) => set("tone", e.target.value)} />
            </Field>
            <Field label="Sign-off" htmlFor={`${id}-sign-off`}>
              <Input id={`${id}-sign-off`} value={draft.sign_off} maxLength={300} onChange={(e) => set("sign_off", e.target.value)} />
            </Field>
          </div>
        </Block>
      )}

      {parts.includes("priorities") && (
        <Block label="Priorities and key people">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Priorities" help="One on each line." htmlFor={`${id}-priorities`}>
              <Textarea id={`${id}-priorities`} value={draft.priorities} onChange={(e) => set("priorities", e.target.value)} />
            </Field>
            <Field label="Key people" help="One on each line." htmlFor={`${id}-people`}>
              <Textarea id={`${id}-people`} value={draft.key_people} onChange={(e) => set("key_people", e.target.value)} />
            </Field>
          </div>
        </Block>
      )}

      {parts.includes("follow_ups") && (
        <Block label="Follow-ups">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Follow-ups on each item" htmlFor={`${id}-follow-ups`}>
              <NumberSelect id={`${id}-follow-ups`} value={draft.follow_ups} onChange={(v) => set("follow_ups", v)} from={0} to={5} defaultWord="Half-way and on the due date" />
            </Field>
            <Field label="Working days to answer" htmlFor={`${id}-answer`}>
              <NumberSelect id={`${id}-answer`} value={draft.answer_within} onChange={(v) => set("answer_within", v)} from={1} to={5} defaultWord={`Default (${defaults?.answer_within_working_days ?? 1})`} />
            </Field>
            <Field label="Late after working days" htmlFor={`${id}-late`}>
              <NumberSelect id={`${id}-late`} value={draft.late_after} onChange={(v) => set("late_after", v)} from={0} to={10} defaultWord={`Default (${defaults?.late_after_working_days ?? 0})`} />
            </Field>
            <Field label="Notices to one owner a day" htmlFor={`${id}-notices`}>
              <NumberSelect id={`${id}-notices`} value={draft.notices} onChange={(v) => set("notices", v)} from={1} to={10} defaultWord={`Default (${defaults?.notices_per_owner_per_day ?? 3})`} />
            </Field>
          </div>
        </Block>
      )}

      {parts.includes("report") && (
        <Block label="Status report">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="How often" htmlFor={`${id}-frequency`}>
              <Select id={`${id}-frequency`} value={draft.frequency} onChange={(e) => set("frequency", e.target.value)}>
                <option value="">Default ({FREQUENCIES.find((f) => f.value === defaults?.report_frequency)?.word ?? "Weekly"})</option>
                {FREQUENCIES.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.word}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Day" htmlFor={`${id}-day`}>
              <Select id={`${id}-day`} value={draft.day} onChange={(e) => set("day", e.target.value)}>
                <option value="">Default ({dayWord(defaults?.report_day) || "Monday"})</option>
                {WEEKDAYS.map((w) => (
                  <option key={w.day} value={w.day}>
                    {w.word}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Time" htmlFor={`${id}-time`}>
              <Input id={`${id}-time`} type="time" value={draft.time} placeholder={defaults?.report_time} onChange={(e) => set("time", e.target.value)} />
            </Field>
            <Field label="Ask owners working days before" htmlFor={`${id}-ask`}>
              <NumberSelect id={`${id}-ask`} value={draft.ask_before} onChange={(v) => set("ask_before", v)} from={1} to={5} defaultWord={`Default (${defaults?.ask_owners_working_days_before ?? 2})`} />
            </Field>
            <Field label="What it includes" htmlFor={`${id}-includes`}>
              <Textarea id={`${id}-includes`} rows={2} value={draft.includes} maxLength={500} onChange={(e) => set("includes", e.target.value)} />
            </Field>
            <Field label="What it leaves out" htmlFor={`${id}-leaves-out`}>
              <Textarea id={`${id}-leaves-out`} rows={2} value={draft.leaves_out} maxLength={500} onChange={(e) => set("leaves_out", e.target.value)} />
            </Field>
          </div>
        </Block>
      )}
    </div>
  );
}
